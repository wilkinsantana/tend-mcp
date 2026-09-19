"""Inactive, CI-only immutable component-v0.1.0 artifact publication lane.

Builds nothing. Consumes the exact validated UI and two native service packages.
Signing and GitHub access occur only after explicit exact-SHA release approval.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPOSITORY = "wilkinsantana/tend-mcp"
TAG = "component-v0.1.0"
ARTIFACTS = (
    "tend-mcp-ui.zip",
    "service-linux-amd64.zip",
    "service-linux-arm64.zip",
    "runtime-linux-amd64.json",
    "runtime-linux-arm64.json",
)


def command(args: list[str], *, data: bytes | None = None, signing: bool = False) -> bytes:
    env = {key: value for key, value in os.environ.items() if key != "COMPONENT_RELEASE_SIGNING_KEY"}
    if signing:
        env["COMPONENT_RELEASE_SIGNING_KEY"] = os.environ["COMPONENT_RELEASE_SIGNING_KEY"]
        env.pop("GH_TOKEN", None)
    result = subprocess.run(args, input=data, capture_output=True, timeout=300, check=False, env=env)
    if result.returncode:
        raise ValueError("command_refused")
    if len(result.stdout) > 64 * 1024:
        raise ValueError("oversized_receipt")
    return result.stdout.strip()


def digest(path: Path, maximum: int) -> str:
    if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= maximum:
        raise ValueError("invalid_artifact")
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def validate_approval(env: dict[str, str], head: str, dirty: bool) -> str:
    sha = env.get("VERIFIED_SHA", "")
    if (
        not re.fullmatch(r"[0-9a-f]{40}", sha)
        or sha != head
        or sha != env.get("COMPONENT_RELEASE_APPROVED_SHA")
        or dirty
        or env.get("GITEA_EVENT_NAME") != "push"
        or env.get("GITEA_REF") != "refs/heads/main"
    ):
        raise ValueError("release_not_approved")
    return sha


def main() -> None:
    sha = validate_approval(
        dict(os.environ),
        command(["git", "rev-parse", "HEAD"]).decode(),
        bool(command(["git", "status", "--porcelain", "--untracked-files=no"])),
    )
    if not os.environ.get("COMPONENT_RELEASE_SIGNING_KEY") or not os.environ.get("GH_TOKEN"):
        raise ValueError("missing_publisher_credentials")
    # The Gitea job graph supplies the green source gate. Independently require
    # the mirror to contain exactly that SHA before creating a release tag.
    remote = command(["git", "ls-remote", "https://github.com/" + REPOSITORY + ".git", "refs/heads/main"])
    if remote.split()[0].decode() != sha:
        raise ValueError("source_mirror_mismatch")
    directory = Path("dist/component")
    receipt = json.loads((directory / "validated-artifacts.json").read_text())
    if receipt.get("source_sha") != sha or set(receipt.get("sha256", {})) != set(ARTIFACTS[:3]):
        raise ValueError("validation_identity_mismatch")
    for name in ARTIFACTS[:3]:
        if (
            digest(directory / name, 20 * 1024 * 1024 if name.startswith("tend-") else 512 * 1024 * 1024)
            != receipt["sha256"][name]
        ):
            raise ValueError("validated_bytes_changed")
    # Validity is fixed once, only for the first publication. Same-version
    # reruns/partial releases require reconciliation, never refreshed metadata.
    issued = int(time.time())
    for arch in ("amd64", "arm64"):
        metadata = {
            "version": "0.1.0",
            "issued_at": issued,
            "expires_at": issued + 180 * 86400,
            "min_core_version": "0.1.0",
            "max_core_version": "0.1.0",
            "platform": "linux/" + arch,
            "capabilities": ["mcp.apps.summary.read", "mcp.deployments.status.read"],
        }
        envelope = command(
            [
                sys.executable,
                "scripts/sign-runtime-release.py",
                "--ui-artifact",
                str(directory / ARTIFACTS[0]),
                "--service-artifact",
                str(directory / f"service-linux-{arch}.zip"),
            ],
            data=json.dumps(metadata).encode(),
            signing=True,
        )
        with (directory / f"runtime-linux-{arch}.json").open("xb") as target:
            target.write(envelope + b"\n")
    expected = {name: digest(directory / name, 512 * 1024 * 1024) for name in ARTIFACTS}
    if any(expected[name] != receipt["sha256"][name] for name in ARTIFACTS[:3]):
        raise ValueError("validated_bytes_changed")
    # No overwrite/clobber, no arbitrary destination, and no raw remote errors.
    command(
        ["gh", "api", "--method", "POST", f"repos/{REPOSITORY}/git/refs", "-f", f"ref=refs/tags/{TAG}", "-f", f"sha={sha}"]
    )
    command(
        [
            "gh",
            "release",
            "create",
            TAG,
            "--repo",
            REPOSITORY,
            "--verify-tag",
            "--draft",
            "--title",
            "Tend MCP 0.1.0",
            "--notes",
            f"Read-only component. Source: {sha}. Core compatibility: 0.1.0 only.",
        ]
    )
    command(["gh", "release", "upload", TAG, "--repo", REPOSITORY, *[str(directory / name) for name in ARTIFACTS]])
    with tempfile.TemporaryDirectory(prefix="mcp-release-verify-") as temporary:
        command(["gh", "release", "download", TAG, "--repo", REPOSITORY, "--dir", temporary])
        if set(os.listdir(temporary)) != set(ARTIFACTS):
            raise ValueError("unexpected_release_assets")
        for name in ARTIFACTS:
            if digest(Path(temporary) / name, 512 * 1024 * 1024) != expected[name]:
                raise ValueError("uploaded_bytes_mismatch")
    command(["gh", "release", "edit", TAG, "--repo", REPOSITORY, "--draft=false"])
    print("Component artifacts published and downloaded bytes verified; not installed or deployed.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.SubprocessError, IndexError, KeyError):
        print("Component publication refused; reconcile any retained tag/draft/assets before retry.", file=sys.stderr)
        raise SystemExit(2) from None
