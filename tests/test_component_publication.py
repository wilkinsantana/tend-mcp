"""Offline gate tests; never read keys, invoke signing, or contact a publisher."""

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "publish-component-release.py"
spec = importlib.util.spec_from_file_location("component_publisher", SCRIPT)
assert spec and spec.loader
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)
SHA = "a" * 40


def approval():
    return {
        "VERIFIED_SHA": SHA,
        "COMPONENT_RELEASE_APPROVED_SHA": SHA,
        "GITEA_EVENT_NAME": "push",
        "GITEA_REF": "refs/heads/main",
    }


def test_exact_main_approval_only():
    assert publisher.validate_approval(approval(), SHA, False) == SHA


@pytest.mark.parametrize(
    "key,value",
    [
        ("VERIFIED_SHA", "main"),
        ("VERIFIED_SHA", "b" * 40),
        ("COMPONENT_RELEASE_APPROVED_SHA", ""),
        ("COMPONENT_RELEASE_APPROVED_SHA", "b" * 40),
        ("GITEA_EVENT_NAME", "pull_request"),
        ("GITEA_EVENT_NAME", "workflow_dispatch"),
        ("GITEA_REF", "refs/heads/feature"),
    ],
)
def test_unapproved_source_refused(key, value):
    env = approval()
    env[key] = value
    with pytest.raises(ValueError, match="release_not_approved"):
        publisher.validate_approval(env, SHA, False)


@pytest.mark.parametrize("head,dirty", [("b" * 40, False), (SHA, True)])
def test_changed_candidate_refused(head, dirty):
    with pytest.raises(ValueError, match="release_not_approved"):
        publisher.validate_approval(approval(), head, dirty)


def test_fixed_names_and_tag():
    assert publisher.TAG == "component-v0.1.0"
    assert publisher.ARTIFACTS == (
        "tend-mcp-ui.zip",
        "service-linux-amd64.zip",
        "service-linux-arm64.zip",
        "runtime-linux-amd64.json",
        "runtime-linux-arm64.json",
    )


def test_digest_refuses_nonregular_symlink_empty_and_oversize(tmp_path):
    path = tmp_path / "artifact"
    for data in (b"", b"x" * 11):
        path.write_bytes(data)
        with pytest.raises(ValueError):
            publisher.digest(path, 10)
    path.unlink()
    path.symlink_to(SCRIPT)
    with pytest.raises(ValueError):
        publisher.digest(path, 100000)


def test_subprocess_secret_separation(monkeypatch):
    monkeypatch.setenv("COMPONENT_RELEASE_SIGNING_KEY", "test-not-a-key")
    monkeypatch.setenv("GH_TOKEN", "test-not-a-token")
    calls = []

    def run(args, **kwargs):
        calls.append(kwargs["env"])
        return type("Result", (), {"returncode": 0, "stdout": b"ok"})()

    monkeypatch.setattr(publisher.subprocess, "run", run)
    publisher.command(["gh", "test"])
    publisher.command(["signer"], signing=True)
    assert "COMPONENT_RELEASE_SIGNING_KEY" not in calls[0]
    assert "GH_TOKEN" not in calls[1]


def test_post_sign_mutation_refuses_before_any_remote_mutation(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for key, value in approval().items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("COMPONENT_RELEASE_SIGNING_KEY", "test-not-a-key")
    monkeypatch.setenv("GH_TOKEN", "test-not-a-token")
    directory = tmp_path / "dist" / "component"
    directory.mkdir(parents=True)
    for name in publisher.ARTIFACTS[:3]:
        (directory / name).write_bytes(b"inert-validated-bytes")
    (directory / "validated-artifacts.json").write_text(
        json.dumps(
            {
                "source_sha": SHA,
                "sha256": {name: publisher.digest(directory / name, 100) for name in publisher.ARTIFACTS[:3]},
            }
        )
    )
    calls = []

    def command(args, **kwargs):
        calls.append(args)
        if args[:3] == ["git", "rev-parse", "HEAD"]:
            return SHA.encode()
        if args[:2] == ["git", "status"]:
            return b""
        if args[:2] == ["git", "ls-remote"]:
            return (SHA + "\trefs/heads/main").encode()
        if kwargs.get("signing"):
            (directory / publisher.ARTIFACTS[0]).write_bytes(b"mutated-after-validation")
            return b"{}"
        raise AssertionError("Unexpected remote mutation")

    monkeypatch.setattr(publisher, "command", command)
    with pytest.raises(ValueError, match="validated_bytes_changed"):
        publisher.main()
    assert not any(args[0] == "gh" for args in calls)
