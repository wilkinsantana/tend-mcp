"""Deterministic native schema-2 UI package; trusted, immutable source workspace."""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import stat
import tempfile
import zipfile
from pathlib import Path

MAX_MODULE = 1024 * 1024


def package(source: Path, output: Path) -> str:
    # Fixed single-module allowlist: no uploaded scripts or dependency resolution.
    if source.is_symlink() or not source.is_dir():
        raise ValueError("invalid_source")
    fd = os.open(source / "index.js", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= MAX_MODULE:
            raise ValueError("invalid_module")
        module = stream.read(MAX_MODULE + 1)
        after = os.fstat(stream.fileno())
        fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if any(getattr(before, field) != getattr(after, field) for field in fields) or len(module) != before.st_size:
            raise ValueError("source_changed")
    module.decode("utf-8", errors="strict")
    manifest = {
        "schema": 2,
        "id": "host.tend.mcp",
        "name": "Tend MCP",
        "version": "0.1.0",
        "description": "Read-only assistant access to explicitly selected apps.",
        "permissions": ["network"],
        "ui": {"module": "index.js", "mount": "tool-window", "size": {"w": 720, "h": 640}},
        "runtime": {"api": 1, "kind": "utility", "modules": []},
        "integrity": {"index.js": "sha256-" + base64.b64encode(hashlib.sha256(module).digest()).decode("ascii")},
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", allowZip64=False) as archive:
        for name, data in (
            ("extension.json", json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("ascii")),
            ("index.js", module),
        ):
            entry = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            entry.create_system = 3
            entry.external_attr = 0o100600 << 16
            entry.compress_type = zipfile.ZIP_STORED
            archive.writestr(entry, data)
    payload = buffer.getvalue()
    # Atomically publish without replacing any existing destination, including a symlink.
    fd, temporary = tempfile.mkstemp(prefix=".mcp-ui-", dir=output.parent)
    try:
        with os.fdopen(fd, "wb") as target:
            target.write(payload)
            target.flush()
            os.fsync(target.fileno())
        os.link(temporary, output, follow_symlinks=False)
        directory = os.open(output.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        os.unlink(temporary)
    return hashlib.sha256(payload).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("ui"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        digest = package(args.source, args.output)
    except (OSError, ValueError):
        # An fsync failure may leave a complete file. Never retry by overwriting it.
        parser.exit(2, "UI packaging refused; reconcile any existing output.\n")
    print(digest)


if __name__ == "__main__":
    main()
