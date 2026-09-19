"""Native UI transport identity and refusal tests (no core source imports)."""

import base64
import hashlib
import importlib.util
import json
import zipfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "pack-native-ui.py"
spec = importlib.util.spec_from_file_location("ui_packager", SCRIPT)
assert spec and spec.loader
packager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(packager)


def test_deterministic_native_manifest_and_integrity(tmp_path):
    source = SCRIPT.parent.parent / "ui"
    first, second = tmp_path / "first.zip", tmp_path / "second.zip"
    digest = packager.package(source, first)
    assert packager.package(source, second) == digest
    assert first.read_bytes() == second.read_bytes()
    assert hashlib.sha256(first.read_bytes()).hexdigest() == digest
    with zipfile.ZipFile(first) as archive:
        assert archive.namelist() == ["extension.json", "index.js"]
        manifest = json.loads(archive.read("extension.json"))
        assert manifest["schema"] == 2
        assert manifest["id"] == "host.tend.mcp"
        assert manifest["version"] == "0.1.0"
        assert manifest["permissions"] == ["network"]
        assert manifest["ui"]["mount"] == "tool-window"
        assert manifest["runtime"] == {"api": 1, "kind": "utility", "modules": []}
        assert manifest["integrity"] == {
            "index.js": "sha256-" + base64.b64encode(hashlib.sha256(archive.read("index.js")).digest()).decode()
        }
        for entry in archive.infolist():
            assert entry.date_time == (1980, 1, 1, 0, 0, 0)
            assert entry.compress_type == zipfile.ZIP_STORED
            assert not entry.extra and not entry.comment


@pytest.mark.parametrize("kind", ["existing", "symlink"])
def test_never_overwrites_output(tmp_path, kind):
    target = tmp_path / "out.zip"
    original = tmp_path / "original"
    original.write_bytes(b"preserved")
    if kind == "symlink":
        target.symlink_to(original)
    else:
        target.write_bytes(b"preserved")
    with pytest.raises(FileExistsError):
        packager.package(SCRIPT.parent.parent / "ui", target)
    assert target.read_bytes() == b"preserved"
    assert not list(tmp_path.glob(".mcp-ui-*"))


@pytest.mark.parametrize("kind", ["empty", "oversize", "symlink", "directory", "invalid_utf8"])
def test_invalid_module_refused(tmp_path, kind):
    source = tmp_path / "ui"
    source.mkdir()
    module = source / "index.js"
    if kind == "directory":
        module.mkdir()
    elif kind == "symlink":
        module.symlink_to(SCRIPT)
    else:
        module.write_bytes({"empty": b"", "oversize": b"x" * (packager.MAX_MODULE + 1), "invalid_utf8": b"\xff"}[kind])
    with pytest.raises((OSError, ValueError)):
        packager.package(source, tmp_path / "out.zip")
    assert not (tmp_path / "out.zip").exists()
