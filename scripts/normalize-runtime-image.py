"""Normalize a trusted local Docker-save build output; never extract layer TARs.

Emits a fresh OCI layout and a tag-free Docker-load archive for that exact image.
The build workspace is trusted. Neither output is signed or installed here.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import tarfile
import zlib
from contextlib import nullcontext
from pathlib import Path
from typing import Any

MAX_BYTES = 512 * 1024 * 1024
ENTRYPOINT = ["/usr/local/bin/python", "-I", "-u", "-m", "tend_mcp.component_worker"]
# Stable exit codes let the fixture identify rejection without relaying raw
# subprocess output, archive names, metadata or filesystem exception messages.
ERROR_EXIT_CODES = {
    "invalid_build_paths": 20,
    "invalid_build_archive": 21,
    "oversized_build_member": 22,
    "invalid_build_metadata": 23,
    "single_image_required": 24,
    "invalid_build_layers": 25,
    "unsupported_build_platform": 26,
    "wrong_build_entrypoint": 27,
    "unsafe_build_config": 28,
    "invalid_build_layer": 29,
    "truncated_build_layer": 30,
    "build_layer_digest_mismatch": 31,
    "invalid_compressed_build_layer": 32,
    "oversized_normalized_layers": 33,
}


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def normalize(source: Path, layout: Path, load_archive: Path) -> str:
    if not 0 < source.stat().st_size <= MAX_BYTES or layout.exists() or load_archive.exists():
        raise ValueError("invalid_build_paths")
    with tarfile.open(source, "r:") as saved:
        members: dict[str, tarfile.TarInfo] = {}
        for member in saved:
            if len(members) >= 128 or member.name in members or (not member.isfile() and not member.isdir()):
                raise ValueError("invalid_build_archive")
            if member.size > MAX_BYTES:
                raise ValueError("oversized_build_member")
            members[member.name] = member

        def read_json(name: str) -> Any:
            member = members[name]
            if not member.isfile() or not 0 < member.size <= 1024 * 1024:
                raise ValueError("invalid_build_metadata")
            stream = saved.extractfile(member)
            assert stream is not None
            with stream:
                return json.load(stream)

        manifests = read_json("manifest.json")
        if not isinstance(manifests, list) or len(manifests) != 1:
            raise ValueError("single_image_required")
        item = manifests[0]
        old = read_json(item["Config"])
        layers = item["Layers"]
        if not isinstance(layers, list) or not 1 <= len(layers) <= 64 or len(set(layers)) != len(layers):
            raise ValueError("invalid_build_layers")
        if old["os"] != "linux" or old["architecture"] not in ("amd64", "arm64"):
            raise ValueError("unsupported_build_platform")
        runtime = old["config"]
        if (
            runtime.get("Entrypoint") != ENTRYPOINT
            or runtime.get("User") != "65532:65532"
            or runtime.get("WorkingDir") != "/app"
        ):
            raise ValueError("wrong_build_entrypoint")
        if runtime.get("Cmd") not in ([], None) or any(
            runtime.get(k) for k in ("Volumes", "ExposedPorts", "OnBuild", "Healthcheck")
        ):
            raise ValueError("unsafe_build_config")
        layout.mkdir(mode=0o700)
        blobs = layout / "blobs" / "sha256"
        blobs.mkdir(parents=True, mode=0o700)

        def blob(value: bytes, media_type: str) -> dict[str, Any]:
            digest = hashlib.sha256(value).hexdigest()
            with (blobs / digest).open("xb") as destination:
                destination.write(value)
            return {"mediaType": media_type, "digest": "sha256:" + digest, "size": len(value)}

        descriptors: list[dict[str, Any]] = []
        total_layer_bytes = 0
        for number, name in enumerate(layers):
            member = members[name]
            if not member.isfile() or not 0 < member.size <= MAX_BYTES:
                raise ValueError("invalid_build_layer")
            temporary = layout / f"layer-{number}.tmp"
            stream = saved.extractfile(member)
            assert stream is not None
            digest = hashlib.sha256()
            count = 0
            with stream, temporary.open("xb") as destination:
                compressed = stream.read(2) == b"\x1f\x8b"
                stream.seek(0)
                # Containerd-backed Docker saves may retain gzip OCI blobs.
                # rootfs.diff_ids always identify *uncompressed* layer bytes.
                # Never extract their TAR members or relax the diff-ID check.
                try:
                    with gzip.GzipFile(fileobj=stream, mode="rb") if compressed else nullcontext(stream) as content:
                        while chunk := content.read(min(1024 * 1024, MAX_BYTES - total_layer_bytes + 1)):
                            count += len(chunk)
                            total_layer_bytes += len(chunk)
                            if total_layer_bytes > MAX_BYTES:
                                raise ValueError("oversized_normalized_layers")
                            destination.write(chunk)
                            digest.update(chunk)
                except (EOFError, gzip.BadGzipFile, zlib.error):
                    raise ValueError("invalid_compressed_build_layer") from None
            if not count:
                raise ValueError("invalid_build_layer")
            if not compressed and count != member.size:
                raise ValueError("truncated_build_layer")
            name_digest = digest.hexdigest()
            os.link(temporary, blobs / name_digest)
            temporary.unlink()
            descriptors.append(
                {"mediaType": "application/vnd.oci.image.layer.v1.tar", "digest": "sha256:" + name_digest, "size": count}
            )
        if old["rootfs"] != {"type": "layers", "diff_ids": [d["digest"] for d in descriptors]}:
            raise ValueError("build_layer_digest_mismatch")
        config = {
            "architecture": old["architecture"],
            "os": "linux",
            "rootfs": old["rootfs"],
            "config": {
                "User": "65532:65532",
                "WorkingDir": "/app",
                "Entrypoint": ENTRYPOINT,
                "Cmd": [],
                "Env": ["PATH=/usr/local/bin:/usr/bin:/bin", "LANG=C.UTF-8"],
            },
        }
        config_bytes = canonical(config)
        config_descriptor = blob(config_bytes, "application/vnd.oci.image.config.v1+json")
        manifest = blob(
            canonical(
                {
                    "schemaVersion": 2,
                    "mediaType": "application/vnd.oci.image.manifest.v1+json",
                    "config": config_descriptor,
                    "layers": descriptors,
                }
            ),
            "application/vnd.oci.image.manifest.v1+json",
        )
        manifest["platform"] = {"os": "linux", "architecture": old["architecture"]}
        (layout / "index.json").write_bytes(
            canonical({"schemaVersion": 2, "mediaType": "application/vnd.oci.image.index.v1+json", "manifests": [manifest]})
        )
        (layout / "oci-layout").write_bytes(canonical({"imageLayoutVersion": "1.0.0"}))
        image_id = config_descriptor["digest"]
        with (
            load_archive.open("xb") as target,
            tarfile.open(fileobj=target, mode="w", format=tarfile.USTAR_FORMAT) as output,
        ):

            def add(name: str, value: bytes) -> None:
                entry = tarfile.TarInfo(name)
                entry.size = len(value)
                entry.mode = 0o600
                output.addfile(entry, io.BytesIO(value))

            config_name = image_id.removeprefix("sha256:") + ".json"
            layer_names = [d["digest"].removeprefix("sha256:") + "/layer.tar" for d in descriptors]
            add("manifest.json", canonical([{"Config": config_name, "RepoTags": [], "Layers": layer_names}]))
            add(config_name, config_bytes)
            for descriptor, name in zip(descriptors, layer_names, strict=True):
                entry = tarfile.TarInfo(name)
                entry.mode = 0o600
                entry.size = descriptor["size"]
                with (blobs / descriptor["digest"].removeprefix("sha256:")).open("rb") as data:
                    output.addfile(entry, data)
        return image_id


def main() -> None:
    parser = argparse.ArgumentParser(description="Normalize trusted Docker-save worker build output")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--layout", type=Path, required=True)
    parser.add_argument("--load-archive", type=Path, required=True)
    args = parser.parse_args()
    try:
        image_id = normalize(args.source, args.layout, args.load_archive)
    except Exception as error:
        # The CLI never forwards unexpected exception messages or tracebacks.
        # Interrupts/SystemExit still propagate; only known exact ValueErrors
        # receive a specific rejection code.
        reason = error.args[0] if type(error) is ValueError and error.args else None
        code = ERROR_EXIT_CODES.get(reason, 2) if isinstance(reason, str) else 2
        parser.exit(code, "Worker image normalization failed; discard this disposable build workspace.\n")
    print(image_id)


if __name__ == "__main__":
    main()
