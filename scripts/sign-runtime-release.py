"""Offline publisher for TEND MCP runtime-release-v1 metadata.

Read bounded public metadata from stdin. Hash the exact local build artifacts;
read the Ed25519 key only from COMPONENT_RELEASE_SIGNING_KEY (canonical base64
of 32 raw private-key bytes). Write only the public signed envelope to stdout.
Never generate keys, configure host trust, upload files or activate a component.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import stat
import sys
import time
from pathlib import Path
from typing import Any, Never

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

DOMAIN = b"tend-mcp-runtime-release-v1\n"
MAX_METADATA_BYTES = 16 * 1024
MAX_VALIDITY_SECONDS = 365 * 24 * 60 * 60
MAX_UI_BYTES = 20 * 1024 * 1024
MAX_SERVICE_BYTES = 512 * 1024 * 1024
CAPABILITIES = {"mcp.apps.summary.read", "mcp.deployments.status.read", "mcp.servers.health.read"}
FIELDS = {"version", "issued_at", "expires_at", "min_core_version", "max_core_version", "platform", "capabilities"}
VERSION = re.compile(r"(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})")


class PublisherError(ValueError):
    pass


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        # Never echo an accidentally supplied credential or private file path.
        self.exit(2, "Runtime release signing refused: invalid_arguments\n")


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")


def version(value: object) -> tuple[int, int, int]:
    if not isinstance(value, str) or (match := VERSION.fullmatch(value)) is None:
        raise PublisherError("invalid_version")
    major, minor, patch = match.groups()
    return int(major), int(minor), int(patch)


def unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PublisherError("duplicate_metadata_field")
        result[key] = value
    return result


def reject_constant(value: str) -> None:
    raise PublisherError("invalid_metadata_json")


def parse_metadata(body: bytes, *, now: int) -> dict[str, Any]:
    if not 1 <= len(body) <= MAX_METADATA_BYTES:
        raise PublisherError("invalid_metadata_size")
    try:
        value = json.loads(body.decode("utf-8"), object_pairs_hook=unique_pairs, parse_constant=reject_constant)
    except (ValueError, UnicodeError, RecursionError):
        raise PublisherError("invalid_metadata_json") from None
    if not isinstance(value, dict) or set(value) != FIELDS:
        raise PublisherError("invalid_metadata_fields")
    version(value["version"])
    if version(value["min_core_version"]) > version(value["max_core_version"]):
        raise PublisherError("invalid_core_range")
    issued, expires = value["issued_at"], value["expires_at"]
    if (
        type(issued) is not int
        or type(expires) is not int
        or type(now) is not int
        or not 0 <= issued <= now < expires <= 10**18
        or not 0 < expires - issued <= MAX_VALIDITY_SECONDS
    ):
        raise PublisherError("invalid_validity_window")
    if value["platform"] not in ("linux/amd64", "linux/arm64"):
        raise PublisherError("unsupported_platform")
    caps = value["capabilities"]
    if (
        not isinstance(caps, list)
        or not 1 <= len(caps) <= len(CAPABILITIES)
        or any(not isinstance(c, str) or c not in CAPABILITIES for c in caps)
        or caps != sorted(set(caps))
    ):
        raise PublisherError("unsupported_capabilities")
    return value


def artifact_digest(path: Path, maximum: int) -> str:
    """Read one regular build output without following a leaf symlink.

    This is a trusted CI workspace, not a general filesystem capability.
    Refuse platforms missing no-follow/nonblocking open rather than weakening
    the checks. Core must independently verify all received bytes.
    """
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_NONBLOCK"):
        raise PublisherError("unsupported_publisher_platform")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as source:
            before = os.fstat(source.fileno())
            if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= maximum:
                raise PublisherError("invalid_artifact_file")
            digest = hashlib.sha256()
            count = 0
            while chunk := source.read(1024 * 1024):
                count += len(chunk)
                if count > maximum:
                    raise PublisherError("artifact_too_large")
                digest.update(chunk)
            after = os.fstat(source.fileno())
            if (
                count != before.st_size
                or after.st_size != before.st_size
                or after.st_mtime_ns != before.st_mtime_ns
                or after.st_ctime_ns != before.st_ctime_ns
            ):
                raise PublisherError("artifact_changed_during_read")
            return digest.hexdigest()
    except OSError:
        raise PublisherError("artifact_read_failed") from None


def create_envelope(metadata: bytes, *, ui: Path, service: Path, signing_key: str, now: int) -> bytes:
    payload = parse_metadata(metadata, now=now)
    try:
        if not isinstance(signing_key, str) or len(signing_key) != 44:
            raise ValueError
        raw_key = base64.b64decode(signing_key, validate=True)
        if len(raw_key) != 32 or base64.b64encode(raw_key).decode("ascii") != signing_key:
            raise ValueError
        private = Ed25519PrivateKey.from_private_bytes(raw_key)
    except (ValueError, TypeError):
        raise PublisherError("invalid_signing_key") from None
    major, minor, patch = version(payload["version"])
    payload.update(
        component_id="host.tend.mcp",
        runtime_profile="docker-isolated-stdio-v1",
        sequence=major * 10**12 + minor * 10**6 + patch,
        ui_sha256=artifact_digest(ui, MAX_UI_BYTES),
        service_sha256=artifact_digest(service, MAX_SERVICE_BYTES),
    )
    public = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    envelope = {
        "schema": 1,
        "key_id": hashlib.sha256(public).hexdigest()[:16],
        "payload": payload,
        "signature": base64.b64encode(private.sign(DOMAIN + canonical(payload))).decode("ascii"),
    }
    return canonical(envelope)


def main() -> None:
    parser = SafeArgumentParser(description="Sign bounded TEND MCP runtime metadata offline")
    parser.add_argument("--ui-artifact", type=Path, required=True)
    parser.add_argument("--service-artifact", type=Path, required=True)
    args = parser.parse_args()
    try:
        envelope = create_envelope(
            sys.stdin.buffer.read(MAX_METADATA_BYTES + 1),
            ui=args.ui_artifact,
            service=args.service_artifact,
            signing_key=os.environ.get("COMPONENT_RELEASE_SIGNING_KEY", ""),
            now=int(time.time()),
        )
    except PublisherError as error:
        print(f"Runtime release signing refused: {error}", file=sys.stderr)
        raise SystemExit(2) from None
    sys.stdout.buffer.write(envelope + b"\n")


if __name__ == "__main__":
    main()
