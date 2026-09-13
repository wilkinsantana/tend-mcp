"""Offline publisher checks use ephemeral keys, never production credentials."""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "sign-runtime-release.py"
spec = importlib.util.spec_from_file_location("release_publisher", SCRIPT)
assert spec and spec.loader
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)
NOW = 1_800_000_000


@pytest.fixture
def artifacts(tmp_path):
    ui, service = tmp_path / "ui.bin", tmp_path / "service.bin"
    ui.write_bytes(b"example-ui")
    service.write_bytes(b"example-service")
    return ui, service


@pytest.fixture
def key():
    private = Ed25519PrivateKey.generate()
    encoded = base64.b64encode(private.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())).decode("ascii")
    return private, encoded


def metadata(**changes):
    return json.dumps(
        {
            "version": "1.2.3",
            "issued_at": NOW - 60,
            "expires_at": NOW + 60,
            "min_core_version": "1.0.0",
            "max_core_version": "2.0.0",
            "platform": "linux/amd64",
            "capabilities": ["mcp.apps.summary.read"],
            **changes,
        }
    ).encode()


def build(artifacts, key, data=None):
    return publisher.create_envelope(
        metadata() if data is None else data,
        ui=artifacts[0],
        service=artifacts[1],
        signing_key=key[1],
        now=NOW,
    )


def test_envelope_binds_actual_artifacts_and_exact_protocol_domain(artifacts, key):
    envelope = json.loads(build(artifacts, key))
    claim = envelope["payload"]
    assert claim["component_id"] == "host.tend.mcp"
    assert claim["runtime_profile"] == "docker-isolated-stdio-v1"
    assert claim["sequence"] == 1_000_002_000_003
    assert claim["ui_sha256"] == hashlib.sha256(b"example-ui").hexdigest()
    assert claim["service_sha256"] == hashlib.sha256(b"example-service").hexdigest()
    canonical = json.dumps(claim, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    key[0].public_key().verify(base64.b64decode(envelope["signature"]), b"tend-mcp-runtime-release-v1\n" + canonical)
    assert key[1].encode() not in build(artifacts, key)


@pytest.mark.parametrize(
    "changes",
    [
        {"component_id": "host.tend.notes"},
        {"ui_sha256": "a" * 64},
        {"command": "untrusted"},
        {"version": "01.2.3"},
        {"version": "1.2.3-beta"},
        {"issued_at": True},
        {"expires_at": NOW},
        {"issued_at": NOW + 1},
        {"expires_at": NOW + publisher.MAX_VALIDITY_SECONDS + 1},
        {"min_core_version": "3.0.0"},
        {"platform": "windows/amd64"},
        {"capabilities": ["mcp.exec"]},
        {"capabilities": []},
        {"capabilities": ["mcp.apps.summary.read", "mcp.apps.summary.read"]},
    ],
)
def test_unreviewed_or_invalid_claims_cannot_be_signed(artifacts, key, changes):
    with pytest.raises(publisher.PublisherError):
        build(artifacts, key, metadata(**changes))


@pytest.mark.parametrize("body", [b"", b"null", b"\xff", b'{"x":1,"x":2}', b'{"x":NaN}', b"[" * 2000 + b"]" * 2000])
def test_strict_metadata_json(artifacts, key, body):
    with pytest.raises(publisher.PublisherError):
        build(artifacts, key, body)


@pytest.mark.parametrize("bad_key", ["", "not-base64", base64.b64encode(b"short").decode()])
def test_invalid_key_is_refused_without_echoing_it(artifacts, key, bad_key):
    with pytest.raises(publisher.PublisherError, match="^invalid_signing_key$"):
        build(artifacts, (key[0], bad_key))


def test_empty_oversize_symlink_and_nonregular_files_are_refused(artifacts, tmp_path):
    path = tmp_path / "empty"
    path.touch()
    with pytest.raises(publisher.PublisherError):
        publisher.artifact_digest(path, 10)
    with pytest.raises(publisher.PublisherError):
        publisher.artifact_digest(artifacts[0], 1)
    link = tmp_path / "link"
    link.symlink_to(artifacts[0])
    with pytest.raises(publisher.PublisherError):
        publisher.artifact_digest(link, 100)
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    with pytest.raises(publisher.PublisherError):
        publisher.artifact_digest(fifo, 100)


def test_changed_file_is_refused(artifacts, monkeypatch):
    original = publisher.os.fstat
    calls = 0

    def changed(fd):
        nonlocal calls
        calls += 1
        if calls == 2:
            artifacts[0].write_bytes(b"changed-length")
        return original(fd)

    monkeypatch.setattr(publisher.os, "fstat", changed)
    with pytest.raises(publisher.PublisherError, match="artifact_changed_during_read"):
        publisher.artifact_digest(artifacts[0], 100)


def test_cli_has_no_key_argument_and_emits_only_public_envelope(artifacts, key):
    now = int(time.time())
    command = [sys.executable, str(SCRIPT), "--ui-artifact", str(artifacts[0]), "--service-artifact", str(artifacts[1])]
    result = subprocess.run(
        command,
        input=metadata(issued_at=now - 1, expires_at=now + 60),
        capture_output=True,
        env={**os.environ, "COMPONENT_RELEASE_SIGNING_KEY": key[1]},
        check=False,
    )
    assert result.returncode == 0 and result.stderr == b""
    assert json.loads(result.stdout)["schema"] == 1
    assert key[1].encode() not in result.stdout + result.stderr
    result = subprocess.run(
        command,
        input=metadata(issued_at=now - 1, expires_at=now + 60),
        capture_output=True,
        env={**os.environ, "COMPONENT_RELEASE_SIGNING_KEY": "test-not-a-key"},
        check=False,
    )
    assert result.returncode == 2 and result.stdout == b""
    assert result.stderr == b"Runtime release signing refused: invalid_signing_key\n"
    result = subprocess.run(command + ["--key", "do-not-echo-this"], capture_output=True, check=False)
    assert result.returncode == 2 and result.stdout == b""
    assert result.stderr == b"Runtime release signing refused: invalid_arguments\n"
