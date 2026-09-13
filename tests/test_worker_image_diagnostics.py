import importlib.util
import subprocess
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


def test_stage_must_be_fixed_before_command_runs(monkeypatch):
    monkeypatch.setattr(fixture.subprocess, "run", lambda *args, **kwargs: pytest.fail("unexpected command"))
    with pytest.raises(ValueError, match="invalid fixture stage"):
        fixture.command(["unused"], stage="private stage")
