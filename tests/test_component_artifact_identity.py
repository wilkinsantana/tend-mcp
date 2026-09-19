"""Receipt binding tests with inert OCI-shaped ZIPs; no runtime execution."""

import hashlib
import importlib.util
import json
import zipfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "record-component-artifacts.py"
spec = importlib.util.spec_from_file_location("artifact_identity", SCRIPT)
assert spec and spec.loader
recorder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recorder)


def service(path, arch):
    config = json.dumps({"architecture": arch, "os": "linux"}).encode()
    config_hash = hashlib.sha256(config).hexdigest()
    manifest = json.dumps({"config": {"digest": "sha256:" + config_hash}}).encode()
    manifest_hash = hashlib.sha256(manifest).hexdigest()
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("index.json", json.dumps({"manifests": [{"digest": "sha256:" + manifest_hash}]}))
        archive.writestr("blobs/sha256/" + manifest_hash, manifest)
        archive.writestr("blobs/sha256/" + config_hash, config)


@pytest.fixture
def artifacts(tmp_path):
    for arch in ("amd64", "arm64"):
        service(tmp_path / f"service-linux-{arch}.zip", arch)
    with zipfile.ZipFile(tmp_path / "tend-mcp-ui.zip", "w") as archive:
        archive.writestr("extension.json", json.dumps({"id": "host.tend.mcp", "version": "0.1.0", "schema": 2}))
    return tmp_path


def test_exact_transport_hashes_and_source_receipt(artifacts):
    receipt = recorder.record(artifacts, "a" * 40)
    assert receipt["source_sha"] == "a" * 40
    assert len(receipt["sha256"]) == 3
    for name, digest in receipt["sha256"].items():
        assert hashlib.sha256((artifacts / name).read_bytes()).hexdigest() == digest
    assert json.loads((artifacts / "validated-artifacts.json").read_text()) == receipt
    with pytest.raises(FileExistsError):
        recorder.record(artifacts, "a" * 40)


def test_mislabeled_platform_refused(artifacts):
    service(artifacts / "service-linux-arm64.zip", "amd64")
    with pytest.raises(ValueError, match="platform_mismatch"):
        recorder.record(artifacts, "a" * 40)
    assert not (artifacts / "validated-artifacts.json").exists()


def test_oversized_metadata_refused(artifacts):
    with zipfile.ZipFile(artifacts / "tend-mcp-ui.zip", "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("extension.json", " " * 32769)
    with pytest.raises(ValueError, match="oversized_metadata"):
        recorder.record(artifacts, "a" * 40)


def test_source_branch_name_not_immutable(artifacts):
    with pytest.raises(ValueError, match="invalid_source_sha"):
        recorder.record(artifacts, "main")
