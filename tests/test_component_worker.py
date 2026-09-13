import io
import json
import subprocess
import sys

import pytest

from tend_mcp import component_worker as worker


def call(operation="hello", **extra):
    return json.loads(
        worker.handle(json.dumps({"protocol": 1, "request_id": "test", "operation": operation, **extra}).encode())
    )


def test_hello_and_tools_have_no_machine_credentials_or_generic_proxy():
    assert call()["result"]["component_id"] == "host.tend.mcp"
    tools = call("tools.list")["result"]["tools"]
    assert {t["name"] for t in tools} == set(worker.CAPABILITIES)
    assert all(t["annotations"]["readOnlyHint"] for t in tools)


@pytest.mark.parametrize(
    "capability,items",
    [
        ("mcp.apps.summary.read", [{"id": "app-1", "name": "Example", "status": "running"}]),
        ("mcp.deployments.status.read", [{"id": "deploy-1", "app_id": "app-1", "status": "succeeded"}]),
        ("mcp.servers.health.read", [{"id": "server-1", "name": "Example", "health": "healthy"}]),
    ],
)
def test_only_closed_core_projections_are_rendered(capability, items):
    result = call("render", capability=capability, result={"items": items, "has_more": False})["result"]
    assert json.loads(result["content"][0]["text"]) == result["structuredContent"]
    assert result["isError"] is False


@pytest.mark.parametrize(
    "extra",
    [
        {"url": "https://untrusted.example"},
        {"token": "do-not-reflect"},
        {"command": "untrusted"},
        {"protocol": True},
    ],
)
def test_unknown_claims_are_refused(extra):
    request = {"protocol": 1, "request_id": "test", "operation": "hello", **extra}
    result = worker.handle(json.dumps(request).encode())
    assert json.loads(result)["error"] == "invalid_request"
    assert b"do-not-reflect" not in result


@pytest.mark.parametrize("body", [b"{}", b"[]", b'{"x":NaN}', b'{"x":1,"x":2}', b"\xff", b"[" * 2000 + b"]" * 2000])
def test_hostile_json_has_fixed_errors(body):
    assert json.loads(worker.handle(body))["error"] == "invalid_request"


def test_secret_fields_controls_duplicate_ids_and_oversize_projections_refused():
    base = {"id": "app", "name": "Example", "status": "running"}
    for items in [[{**base, "password": "never-reflect"}], [{**base, "name": "bad\nname"}], [base, base], [base] * 101]:
        result = call("render", capability="mcp.apps.summary.read", result={"items": items, "has_more": False})
        assert result["error"] == "invalid_request"
        assert "never-reflect" not in json.dumps(result)


@pytest.mark.parametrize("name", ["\ud800", "\udfff"])
def test_lone_unicode_surrogates_are_refused(name):
    result = call(
        "render",
        capability="mcp.apps.summary.read",
        result={
            "items": [{"id": "app", "name": name, "status": "running"}],
            "has_more": False,
        },
    )
    assert result["error"] == "invalid_request"


def test_valid_supplementary_unicode_is_preserved():
    result = call(
        "render",
        capability="mcp.apps.summary.read",
        result={
            "items": [{"id": "app", "name": "Sprout \U0001f331", "status": "running"}],
            "has_more": False,
        },
    )["result"]
    assert result["structuredContent"]["apps"][0]["name"] == "Sprout \U0001f331"


def test_real_process_probe_and_multiple_frames():
    probe = subprocess.run([sys.executable, "-m", "tend_mcp.component_worker", "--probe"], capture_output=True, check=True)
    assert json.loads(probe.stdout)["request_id"] == "probe" and probe.stderr == b""
    frames = b'{"protocol":1,"request_id":"x","operation":"hello"}\n' * 2
    result = subprocess.run(
        [sys.executable, "-m", "tend_mcp.component_worker"], input=frames, capture_output=True, check=True
    )
    assert len(result.stdout.splitlines()) == 2 and result.stderr == b""


@pytest.mark.parametrize("body", [b"partial", b"x" * (worker.MAX_FRAME + 1)])
def test_bad_framing_terminates_without_draining_unbounded_input(body):
    output = io.BytesIO()
    assert worker.run(io.BytesIO(body), output) == 1
    assert json.loads(output.getvalue())["error"] == "invalid_frame"
