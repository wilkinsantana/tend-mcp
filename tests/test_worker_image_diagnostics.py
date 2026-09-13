import hashlib
import importlib.util
import io
import json
import subprocess
import tarfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check-worker-image.py"
spec = importlib.util.spec_from_file_location("worker_fixture", SCRIPT)
assert spec and spec.loader
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


def test_failed_normalizer_reports_stage_and_exit_not_output(monkeypatch):
    def run(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 31, b"private stdout", b"private stderr")

    monkeypatch.setattr(fixture.subprocess, "run", run)
    with pytest.raises(RuntimeError) as caught:
        fixture.command(["private executable"], stage="normalize")
    assert str(caught.value) == "worker image normalize command failed (exit=31; output suppressed)"


@pytest.mark.parametrize("timeout", [True, False])
@pytest.mark.parametrize("allow_nonzero", [True, False])
def test_launch_failures_do_not_expose_arguments_or_output(monkeypatch, timeout, allow_nonzero):
    def run(*args, **kwargs):
        if timeout:
            raise subprocess.TimeoutExpired(args[0], 1, output=b"private stdout", stderr=b"private stderr")
        raise OSError("private executable path")

    monkeypatch.setattr(fixture.subprocess, "run", run)
    with pytest.raises(RuntimeError) as caught:
        fixture.command(["private executable"], stage="normalize", allow_nonzero=allow_nonzero)
    assert "normalize" in str(caught.value) and "private" not in str(caught.value)
    assert caught.value.__suppress_context__


def test_best_effort_tag_removal_can_ignore_nonzero(monkeypatch):
    monkeypatch.setattr(
        fixture.subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 1, b"", b"private stderr")
    )
    assert fixture.command(["unused"], allow_nonzero=True) == b""


def saved_config(path, raw, *, duplicate=False):
    files = [("manifest.json", json.dumps([{"Config": "config.json", "Layers": []}]).encode()), ("config.json", raw)]
    if duplicate:
        files.append(("config.json", raw))
    with tarfile.open(path, "w") as archive:
        for name, data in files:
            entry = tarfile.TarInfo(name)
            entry.size = len(data)
            archive.addfile(entry, io.BytesIO(data))


@pytest.mark.parametrize("containerd", [False, True])
def test_loaded_digest_is_hint_until_raw_config_is_verified(tmp_path, monkeypatch, containerd):
    raw = b'{"config":{"User":"65532:65532"}}'
    expected = "sha256:" + hashlib.sha256(raw).hexdigest()
    candidate = "sha256:" + "b" * 64 if containerd else expected
    calls = []

    def command(args, **kwargs):
        calls.append(args)
        if args[3:5] == ["image", "inspect"]:
            assert args[-1] == candidate
            return json.dumps([{"Id": candidate, "Config": {"User": "65532:65532"}}]).encode()
        assert args[3] == "save" and args[-1] == candidate
        saved_config(Path(args[5]), raw)
        return b""

    monkeypatch.setattr(fixture, "command", command)
    result = fixture.resolve_loaded_image(f"Loaded image ID: {candidate}\n".encode(), expected, tmp_path)
    assert result == candidate and len(calls) == 2
    assert not (tmp_path / "loaded-verification.tar").exists()


@pytest.mark.parametrize(
    "output", [b"private output", b"Loaded image ID: invalid", (b"Loaded image ID: sha256:" + b"a" * 64 + b"\n") * 2]
)
def test_ambiguous_load_output_cannot_select_an_image(tmp_path, monkeypatch, output):
    monkeypatch.setattr(fixture, "command", lambda *a, **k: pytest.fail("unexpected daemon call"))
    with pytest.raises(RuntimeError, match="loaded image identity unconfirmed"):
        fixture.resolve_loaded_image(output, "sha256:" + "a" * 64, tmp_path)


@pytest.mark.parametrize("duplicate,raw", [(True, b"{}"), (False, b'{"private":"metadata"}')])
def test_exported_raw_config_mismatch_or_duplicate_refuses(tmp_path, duplicate, raw):
    source = tmp_path / "saved.tar"
    saved_config(source, raw, duplicate=duplicate)
    expected = "sha256:" + hashlib.sha256(b"{}").hexdigest()
    with pytest.raises(RuntimeError, match="raw config verification failed") as caught:
        fixture.verify_saved_config(source, expected)
    assert caught.value.__suppress_context__ and "private" not in str(caught.value)


def test_inspection_mismatch_prevents_export(tmp_path, monkeypatch):
    def command(args, **kwargs):
        assert args[3:5] == ["image", "inspect"]
        return b'[{"Id":"wrong","Config":{"User":"0"}}]'

    monkeypatch.setattr(fixture, "command", command)
    with pytest.raises(RuntimeError, match="inspection unconfirmed"):
        fixture.resolve_loaded_image(b"Loaded image ID: sha256:" + b"a" * 64, "sha256:" + "b" * 64, tmp_path)


def test_main_never_creates_worker_when_imported_raw_config_changes(tmp_path, monkeypatch):
    expected = "sha256:" + hashlib.sha256(b"{}").hexdigest()
    candidate = "sha256:" + "b" * 64
    monkeypatch.setattr(fixture.sys, "argv", ["fixture", "--output", str(tmp_path / "output")])
    calls = []

    def command(args, **kwargs):
        calls.append(args)
        if args[:3] != fixture.DOCKER:
            assert args[1].endswith("normalize-runtime-image.py")
            return expected.encode()
        operation = args[3]
        if operation == "load":
            return f"Loaded image ID: {candidate}".encode()
        if args[3:5] == ["image", "inspect"]:
            return json.dumps([{"Id": candidate, "Config": {"User": "65532:65532"}}]).encode()
        if operation == "save":
            if args[-1] == candidate:
                saved_config(Path(args[5]), b'{"changed":true}')
            return b""
        assert operation in {"build", "ps"} or args[3:5] == ["image", "rm"]
        return b""

    monkeypatch.setattr(fixture, "command", command)
    with pytest.raises(RuntimeError, match="raw config verification failed"):
        fixture.main()
    assert not any(args[:3] == fixture.DOCKER and args[3] == "create" for args in calls)
    assert not (tmp_path / "output").exists()


def test_stage_must_be_fixed_before_command_runs(monkeypatch):
    monkeypatch.setattr(fixture.subprocess, "run", lambda *args, **kwargs: pytest.fail("unexpected command"))
    with pytest.raises(ValueError, match="invalid fixture stage"):
        fixture.command(["unused"], stage="private stage")
