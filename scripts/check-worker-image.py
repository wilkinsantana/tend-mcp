"""Opt-in local Docker build/normalize/probe fixture; never uses remote contexts.

Only fixed local Docker socket, job-specific tags/container, no user volumes,
no production keys. Requires a trusted disposable Docker-enabled CI runner.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import secrets
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

DOCKER = ["docker", "--host", "unix:///var/run/docker.sock"]


def command(args: list[str], *, timeout: int = 600, stage: str = "docker", allow_nonzero: bool = False) -> bytes:
    if stage not in {"docker", "build", "save", "normalize", "package", "load", "inspect-image", "verify-save"}:
        raise ValueError("invalid fixture stage")
    try:
        result = subprocess.run(args, capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"worker image {stage} command timed out (output suppressed)") from None
    except OSError:
        raise RuntimeError(f"worker image {stage} command unavailable (details suppressed)") from None
    if result.returncode and not allow_nonzero:
        raise RuntimeError(f"worker image {stage} command failed (exit={result.returncode}; output suppressed)")
    return result.stdout


def verify_saved_config(source: Path, expected: str) -> None:
    """Verify exact raw config bytes, not a normalized projection of them.

    This is a trusted local daemon export. Names are lookup keys only; no TAR
    member is extracted onto the filesystem and layer bodies are not read.
    """
    try:
        if not 0 < source.stat().st_size <= 512 * 1024 * 1024:
            raise ValueError("export size")
        with tarfile.open(source, "r:") as saved:
            members: dict[str, tarfile.TarInfo] = {}
            for member in saved:
                if len(members) >= 128 or member.name in members or not (member.isfile() or member.isdir()):
                    raise ValueError("export structure")
                members[member.name] = member

            def read(name: str) -> bytes:
                member = members[name]
                if not member.isfile() or not 0 < member.size <= 1024 * 1024:
                    raise ValueError("export metadata size")
                stream = saved.extractfile(member)
                if stream is None:
                    raise ValueError("export metadata missing")
                with stream:
                    return stream.read(1024 * 1024 + 1)

            manifest = json.loads(read("manifest.json"))
            if not isinstance(manifest, list) or len(manifest) != 1:
                raise ValueError("single export required")
            raw_config = read(manifest[0]["Config"])
            if "sha256:" + hashlib.sha256(raw_config).hexdigest() != expected:
                raise ValueError("config digest mismatch")
    except Exception:
        raise RuntimeError("loaded image raw config verification failed") from None


def resolve_loaded_image(output: bytes, expected_config: str, work: Path) -> str:
    # Import output is only a lookup hint. Containerd stores identify images
    # by manifest digest; classic stores generally use the config digest.
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", expected_config):
        raise RuntimeError("invalid normalized image identity")
    candidates = [
        match[1].decode("ascii")
        for line in output.splitlines()
        if (match := re.fullmatch(rb"Loaded image ID: (sha256:[0-9a-f]{64})", line.strip()))
    ]
    if len(candidates) != 1:
        raise RuntimeError("loaded image identity unconfirmed")
    candidate = candidates[0]
    try:
        result = json.loads(command(DOCKER + ["image", "inspect", candidate], stage="inspect-image"))
        if len(result) != 1 or result[0]["Id"] != candidate or result[0]["Config"]["User"] != "65532:65532":
            raise ValueError("image mismatch")
    except Exception:
        raise RuntimeError("loaded image inspection unconfirmed") from None
    exported = work / "loaded-verification.tar"
    command(DOCKER + ["save", "-o", str(exported), candidate], stage="verify-save")
    verify_saved_config(exported, expected_config)
    exported.unlink()
    return candidate


def main() -> None:
    parser = argparse.ArgumentParser(description="Build and test the isolated worker image locally")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    nonce = secrets.token_hex(16)
    raw_tag = f"tend-mcp-build:{nonce}"
    scripts = Path(__file__).resolve().parent
    root = scripts.parent
    container_id: str | None = None
    try:
        with tempfile.TemporaryDirectory(prefix="mcp-image-check-") as temporary:
            work = Path(temporary)
            command(DOCKER + ["build", "-f", str(root / "Dockerfile.worker"), "-t", raw_tag, str(root)], stage="build")
            command(DOCKER + ["save", "-o", str(work / "built.tar"), raw_tag], stage="save")
            image_id = (
                command(
                    [
                        sys.executable,
                        str(scripts / "normalize-runtime-image.py"),
                        "--source",
                        str(work / "built.tar"),
                        "--layout",
                        str(work / "layout"),
                        "--load-archive",
                        str(work / "normalized.tar"),
                    ],
                    stage="normalize",
                )
                .decode()
                .strip()
            )
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
                raise RuntimeError("invalid normalized image identity")
            loaded = command(DOCKER + ["load", "-i", str(work / "normalized.tar")], stage="load")
            daemon_image_id = resolve_loaded_image(loaded, image_id, work)
            created = (
                command(
                    DOCKER
                    + [
                        "create",
                        "-i",
                        "--network",
                        "none",
                        "--read-only",
                        "--cap-drop",
                        "ALL",
                        "--security-opt",
                        "no-new-privileges",
                        "--pids-limit",
                        "16",
                        "--memory",
                        "64m",
                        "--cpus",
                        "0.5",
                        "--log-driver",
                        "none",
                        "--label",
                        "tend.mcp.fixture=" + nonce,
                        daemon_image_id,
                    ]
                )
                .decode()
                .strip()
            )
            if len(created) != 64 or any(c not in "0123456789abcdef" for c in created):
                raise RuntimeError("invalid fixture container identity")
            container_id = created
            command(DOCKER + ["start", container_id], timeout=30)
            probe = json.loads(
                command(
                    DOCKER
                    + [
                        "exec",
                        container_id,
                        "/usr/local/bin/python",
                        "-I",
                        "-u",
                        "-m",
                        "tend_mcp.component_worker",
                        "--probe",
                    ],
                    timeout=30,
                )
            )
            status = json.loads(command(DOCKER + ["inspect", container_id]))[0]
            if not status["State"]["Running"] or probe["result"]["component_id"] != "host.tend.mcp":
                raise RuntimeError("worker probe failed")
            host = status["HostConfig"]
            if not host["ReadonlyRootfs"] or host["NetworkMode"] != "none" or status["Mounts"] or host["Privileged"]:
                raise RuntimeError("worker isolation mismatch")
            receipt = command(
                [
                    sys.executable,
                    str(scripts / "pack-runtime-service.py"),
                    "--layout",
                    str(work / "layout"),
                    "--output",
                    str(work / "service.zip"),
                ],
                stage="package",
            )
            args.output.mkdir(parents=True, exist_ok=False)
            shutil.copyfile(work / "service.zip", args.output / "service.zip")
            (args.output / "identity.json").write_text(
                json.dumps({"image_id": image_id, "daemon_image_id": daemon_image_id, "package": json.loads(receipt)})
            )
            print("Worker image built, normalized, isolated probe passed, service package produced.")
    finally:
        # Reconcile by this run's opaque label even if create's reply was lost.
        owned = (
            command(DOCKER + ["ps", "-aq", "--no-trunc", "--filter", "label=tend.mcp.fixture=" + nonce], timeout=30)
            .decode()
            .split()
        )
        for identifier in owned:
            if len(identifier) != 64 or any(c not in "0123456789abcdef" for c in identifier):
                raise RuntimeError("invalid cleanup identity")
            current = json.loads(command(DOCKER + ["inspect", identifier], timeout=30))[0]
            if current["Config"]["Labels"].get("tend.mcp.fixture") != nonce:
                raise RuntimeError("foreign fixture preserved")
            command(DOCKER + ["rm", "-f", identifier], timeout=30)
        if command(DOCKER + ["ps", "-aq", "--filter", "label=tend.mcp.fixture=" + nonce], timeout=30).strip():
            raise RuntimeError("fixture cleanup unconfirmed")
        # Remove only this fixture's unique tags, never prune shared images,
        # caches, volumes, or containers. Shared immutable content may remain.
        command(DOCKER + ["image", "rm", raw_tag], timeout=30, allow_nonzero=True)


if __name__ == "__main__":
    main()
