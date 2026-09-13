"""Real filesystem packaging fixtures are inert, not service images."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "pack-runtime-service.py"
spec = importlib.util.spec_from_file_location("service_packager", SCRIPT)
assert spec and spec.loader
packager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(packager)


@pytest.fixture
def layout(tmp_path):
    root = tmp_path / "layout"
    blobs = root / "blobs" / "sha256"
    blobs.mkdir(parents=True)
    (root / "oci-layout").write_bytes(b'{"imageLayoutVersion":"1.0.0"}')
    (root / "index.json").write_bytes(b"{}")
    (blobs / hashlib.sha256(b"inert").hexdigest()).write_bytes(b"inert")
    return root


def test_deterministic_package_uses_exact_container_profile(layout, tmp_path):
    first, second = tmp_path / "first.zip", tmp_path / "second.zip"
    receipt = packager.pack(layout, first)
    os.utime(layout / "index.json", (100, 100))
    assert packager.pack(layout, second) == receipt
    assert first.read_bytes() == second.read_bytes()
    assert receipt == {
        "format": "service-oci-zip-v1",
        "sha256": hashlib.sha256(first.read_bytes()).hexdigest(),
        "bytes": first.stat().st_size,
    }
    with zipfile.ZipFile(first) as archive:
        assert archive.namelist() == sorted(archive.namelist())
        for entry in archive.infolist():
            assert entry.compress_type == zipfile.ZIP_STORED
            assert entry.external_attr == 0o100600 << 16
            assert entry.extra == b"" and entry.comment == b"" and entry.flag_bits == 0
            assert entry.date_time == (1980, 1, 1, 0, 0, 0)
    assert not list(tmp_path.glob(".mcp-package-*"))


@pytest.mark.parametrize("target_kind", ["regular", "symlink"])
def test_existing_output_is_never_replaced(layout, tmp_path, target_kind):
    target = tmp_path / "existing.zip"
    original = tmp_path / "original"
    original.write_bytes(b"preserve")
    if target_kind == "regular":
        target.write_bytes(b"preserve")
    else:
        target.symlink_to(original)
    with pytest.raises(packager.PackageError):
        packager.pack(layout, target)
    assert target.read_bytes() == b"preserve" and original.read_bytes() == b"preserve"
    assert not list(tmp_path.glob(".mcp-package-*"))


@pytest.mark.parametrize(
    "change", ["root_link", "blob_link", "directory_link", "fifo", "empty", "wrong_digest", "extra", "traversal_name"]
)
def test_unsafe_or_malformed_layouts_leave_no_output(layout, tmp_path, change):
    blob = next((layout / "blobs" / "sha256").iterdir())
    if change == "root_link":
        link = tmp_path / "link"
        link.symlink_to(layout, target_is_directory=True)
        layout = link
    elif change == "directory_link":
        (layout / "blobs").rename(tmp_path / "moved-blobs")
        (layout / "blobs").symlink_to(tmp_path / "moved-blobs", target_is_directory=True)
    elif change == "blob_link":
        blob.unlink()
        blob.symlink_to(layout / "index.json")
    elif change == "fifo":
        blob.unlink()
        os.mkfifo(blob)
    elif change == "empty":
        blob.write_bytes(b"")
    elif change == "wrong_digest":
        blob.write_bytes(b"wrong bytes")
    elif change == "extra":
        (layout / "unexpected").write_bytes(b"unreviewed")
    else:
        blob.rename(blob.parent / "not-a-digest")
    with pytest.raises(packager.PackageError):
        packager.pack(layout, tmp_path / "output.zip")
    assert not (tmp_path / "output.zip").exists()
    assert not list(tmp_path.glob(".mcp-package-*"))


def test_metadata_and_total_budgets_fail_before_publication(layout, tmp_path, monkeypatch):
    (layout / "index.json").write_bytes(b"a" * (packager.MAX_JSON_BYTES + 1))
    with pytest.raises(packager.PackageError, match="oversized_layout_metadata"):
        packager.pack(layout, tmp_path / "output.zip")
    (layout / "index.json").write_bytes(b"{}")
    monkeypatch.setattr(packager, "MAX_BYTES", 100)
    with pytest.raises(packager.PackageError, match="service_package_too_large"):
        packager.pack(layout, tmp_path / "output.zip")
    assert not (tmp_path / "output.zip").exists()


def test_output_cannot_modify_layout(layout):
    with pytest.raises(packager.PackageError, match="output_inside_layout"):
        packager.pack(layout, layout / "output.zip")


def test_source_mutation_is_refused(layout, tmp_path, monkeypatch):
    original = packager.os.stat

    def changed(path, **kwargs):
        if path == "index.json":
            (layout / "index.json").write_bytes(b"changed")
        return original(path, **kwargs)

    monkeypatch.setattr(packager.os, "stat", changed)
    with pytest.raises(packager.PackageError, match="source_changed"):
        packager.pack(layout, tmp_path / "output.zip")
    assert not (tmp_path / "output.zip").exists()


@pytest.mark.parametrize("failure_at", [1, 2])
def test_durability_failure_does_not_claim_success_or_delete_visible_output(layout, tmp_path, monkeypatch, failure_at):
    original = packager.os.fsync
    calls = 0

    def fail(fd):
        nonlocal calls
        calls += 1
        if calls == failure_at:
            raise OSError("private filesystem detail")
        original(fd)

    monkeypatch.setattr(packager.os, "fsync", fail)
    with pytest.raises(packager.PackageError) as error:
        packager.pack(layout, tmp_path / "output.zip")
    assert "private" not in str(error.value)
    assert (tmp_path / "output.zip").exists() == (failure_at == 2)
    assert not list(tmp_path.glob(".mcp-package-*"))


def test_cli_reports_only_public_receipt_and_redacts_arguments(layout, tmp_path):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--layout", str(layout), "--output", str(tmp_path / "output.zip")],
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0 and result.stderr == b""
    assert set(json.loads(result.stdout)) == {"format", "bytes", "sha256"}
    result = subprocess.run([sys.executable, str(SCRIPT), "--secret", "never-echo"], capture_output=True, check=False)
    assert result.returncode == 2 and result.stdout == b""
    assert result.stderr == b"Service packaging refused: invalid_arguments\n"
