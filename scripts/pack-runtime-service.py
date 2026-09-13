"""Deterministic service-oci-zip-v1 packaging in a trusted publisher workspace.

No Docker, network, source imports, key access or runtime activation. OCI graph
and executable-content policy remain core's independent validation boundary.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import stat
import sys
import zipfile
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import BinaryIO, Never

MAX_BYTES = 512 * 1024 * 1024
MAX_JSON_BYTES = 32 * 1024
MAX_ENTRIES = 128
FORMAT = "service-oci-zip-v1"


class PackageError(ValueError):
    pass


class SafeParser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        self.exit(2, "Service packaging refused: invalid_arguments\n")


@contextmanager
def directory(path: str | Path, *, parent: int | None = None) -> Iterator[int]:
    if not all(hasattr(os, name) for name in ("O_DIRECTORY", "O_NOFOLLOW", "O_NONBLOCK")):
        raise PackageError("unsupported_publisher_platform")
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    try:
        yield descriptor
    finally:
        os.close(descriptor)


@contextmanager
def regular(parent: int, name: str) -> Iterator[BinaryIO]:
    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    with os.fdopen(descriptor, "rb") as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_BYTES:
            raise PackageError("invalid_source_file")
        yield source


def identity(info: os.stat_result) -> tuple[int, int, int, int, int]:
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def pack(layout: Path, output: Path) -> dict[str, str | int]:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", output.name):
        raise PackageError("invalid_output_name")
    try:
        with ExitStack() as stack:
            root = stack.enter_context(directory(layout))
            if set(os.listdir(root)) != {"oci-layout", "index.json", "blobs"}:
                raise PackageError("invalid_layout_entries")
            blobs = stack.enter_context(directory("blobs", parent=root))
            if os.listdir(blobs) != ["sha256"]:
                raise PackageError("invalid_blob_layout")
            sha = stack.enter_context(directory("sha256", parent=blobs))
            names = os.listdir(sha)
            if not 1 <= len(names) <= MAX_ENTRIES - 2 or any(not re.fullmatch(r"[0-9a-f]{64}", name) for name in names):
                raise PackageError("invalid_blob_names")
            destination = stack.enter_context(directory(output.parent))
            if any(identity(os.fstat(destination))[:2] == identity(os.fstat(fd))[:2] for fd in (root, blobs, sha)):
                raise PackageError("output_inside_layout")
            sources = []
            for name, parent, leaf in sorted(
                [("index.json", root, "index.json"), ("oci-layout", root, "oci-layout")]
                + [("blobs/sha256/" + name, sha, name) for name in names]
            ):
                source = stack.enter_context(regular(parent, leaf))
                before = os.fstat(source.fileno())
                if parent == root and before.st_size > MAX_JSON_BYTES:
                    raise PackageError("oversized_layout_metadata")
                sources.append((name, parent, leaf, source, before))
            # Exact classic ZIP overhead for this no-extra/no-comment format.
            predicted = 22 + sum(76 + 2 * len(name) + before.st_size for name, _, _, _, before in sources)
            if predicted > MAX_BYTES:
                raise PackageError("service_package_too_large")
            temporary = ".mcp-package-" + secrets.token_hex(16)
            fd = os.open(temporary, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=destination)
            try:
                with os.fdopen(fd, "w+b") as target:
                    with zipfile.ZipFile(target, "w", allowZip64=False) as archive:
                        for name, parent, leaf, source, before in sources:
                            entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                            entry.create_system = 3
                            entry.external_attr = 0o100600 << 16
                            entry.compress_type = zipfile.ZIP_STORED
                            entry.file_size = before.st_size
                            count = 0
                            digest = hashlib.sha256()
                            with archive.open(entry, "w") as writer:
                                while chunk := source.read(1024 * 1024):
                                    count += len(chunk)
                                    if count > before.st_size:
                                        raise PackageError("source_changed")
                                    writer.write(chunk)
                                    digest.update(chunk)
                            if (
                                count != before.st_size
                                or identity(os.fstat(source.fileno())) != identity(before)
                                or identity(os.stat(leaf, dir_fd=parent, follow_symlinks=False)) != identity(before)
                            ):
                                raise PackageError("source_changed")
                            if parent == sha and digest.hexdigest() != leaf:
                                raise PackageError("source_blob_digest_mismatch")
                    target.flush()
                    if target.tell() != predicted:
                        raise PackageError("unexpected_package_size")
                    os.fsync(target.fileno())
                    target.seek(0)
                    package_digest = hashlib.sha256()
                    while chunk := target.read(1024 * 1024):
                        package_digest.update(chunk)
                # Atomic no-overwrite publication: a prior file or symlink is
                # never replaced, even if it appeared after preparation.
                os.link(temporary, output.name, src_dir_fd=destination, dst_dir_fd=destination, follow_symlinks=False)
                try:
                    os.fsync(destination)
                except OSError:
                    # The complete output may be visible. Preserve it; never
                    # claim success or silently delete it after uncertainty.
                    raise PackageError("publication_uncertain") from None
            finally:
                os.unlink(temporary, dir_fd=destination)
            return {"format": FORMAT, "sha256": package_digest.hexdigest(), "bytes": predicted}
    except OSError:
        raise PackageError("package_io_failed") from None


def main() -> None:
    parser = SafeParser(description="Package a normalized OCI layout without execution")
    parser.add_argument("--layout", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = pack(args.layout, args.output)
    except PackageError as error:
        print(f"Service packaging refused: {error}", file=sys.stderr)
        raise SystemExit(2) from None
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
