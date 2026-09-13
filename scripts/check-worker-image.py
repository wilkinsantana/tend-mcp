"""Opt-in local Docker build/normalize/probe fixture; never uses remote contexts.

Only fixed local Docker socket, job-specific tags/container, no user volumes,
no production keys. Requires a trusted disposable Docker-enabled CI runner.
"""

from __future__ import annotations

import argparse
import json
import secrets
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

DOCKER = ["docker", "--host", "unix:///var/run/docker.sock"]


def command(args: list[str], *, timeout: int = 600, stage: str = "docker", allow_nonzero: bool = False) -> bytes:
    if stage not in {"docker", "build", "save", "normalize", "package"}:
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Build and test the isolated worker image locally")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    nonce = secrets.token_hex(16)
    raw_tag, normalized_tag = f"tend-mcp-build:{nonce}", f"tend-mcp-normalized:{nonce}"
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
            if not image_id.startswith("sha256:") or len(image_id) != 71:
                raise RuntimeError("invalid normalized image identity")
            command(DOCKER + ["load", "-i", str(work / "normalized.tar")])
            command(DOCKER + ["tag", image_id, normalized_tag])
            config = json.loads(command(DOCKER + ["image", "inspect", normalized_tag]))[0]
            if config["Id"] != image_id or config["Config"]["User"] != "65532:65532":
                raise RuntimeError("normalized image mismatch")
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
                        normalized_tag,
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
            (args.output / "identity.json").write_text(json.dumps({"image_id": image_id, "package": json.loads(receipt)}))
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
        for tag in (normalized_tag, raw_tag):
            command(DOCKER + ["image", "rm", tag], timeout=30, allow_nonzero=True)


if __name__ == "__main__":
    main()
