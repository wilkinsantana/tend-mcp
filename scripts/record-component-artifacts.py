"""Bind prebuilt platform-checked transport bytes to the immutable CI source."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import zipfile
from pathlib import Path


def record(directory: Path, sha: str) -> dict[str, object]:
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("invalid_source_sha")
    hashes = {}
    for name in ("tend-mcp-ui.zip", "service-linux-amd64.zip", "service-linux-arm64.zip"):
        path = directory / name
        limit = 20 * 1024 * 1024 if name.startswith("tend-") else 512 * 1024 * 1024
        if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= limit:
            raise ValueError("invalid_artifact")
        with zipfile.ZipFile(path) as archive:

            def metadata(member: str) -> dict[str, object]:
                info = archive.getinfo(member)
                if info.file_size > 32768:
                    raise ValueError("oversized_metadata")
                return json.loads(archive.read(info))

            if name.startswith("service-"):
                # Packager/normalizer and native probe already validated graph.
                # Check the platform binding once more before recording names.
                def blob(descriptor: dict[str, str]) -> dict[str, object]:
                    digest = descriptor["digest"]
                    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
                        raise ValueError("invalid_digest")
                    return metadata("blobs/sha256/" + digest[7:])

                index = metadata("index.json")
                manifest = blob(index["manifests"][0])  # type: ignore[index]
                config = blob(manifest["config"])  # type: ignore[arg-type]
                arch = name.removeprefix("service-linux-").removesuffix(".zip")
                if config.get("architecture") != arch or config.get("os") != "linux":
                    raise ValueError("platform_mismatch")
            else:
                manifest = metadata("extension.json")
                if (
                    manifest.get("id") != "host.tend.mcp"
                    or manifest.get("version") != "0.1.0"
                    or manifest.get("schema") != 2
                ):
                    raise ValueError("ui_identity_mismatch")
        with path.open("rb") as stream:
            hashes[name] = hashlib.file_digest(stream, "sha256").hexdigest()
    receipt: dict[str, object] = {"source_sha": sha, "sha256": hashes}
    with (directory / "validated-artifacts.json").open("x") as target:
        json.dump(receipt, target, sort_keys=True)
    return receipt


if __name__ == "__main__":
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    record(Path("dist/component"), sha)
