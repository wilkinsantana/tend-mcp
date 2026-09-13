"""Tools against a fake panel. No network, no real tend.host."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from tend_mcp import tools
from tend_mcp.client import PanelClient, PanelError
from tend_mcp.config import Config, ConfigError
from tend_mcp.server import build_server

APP = {
    "uuid": "a1",
    "name": "blog",
    "server_uuid": "s1",
    "source": "image",
    "source_ref": "ghost:5",
    "build": {"big": "blob"},
    "runtime": {"env": {}},
    "domains": [{"host": "blog.example.com"}],
    "lifecycle": "permanent",
    "auto_deploy": True,
    "tags": [],
    "created_at": 1,
    "last_deployed_at": 2,
}


class FakePanel:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, Any]] = []
        self.scopes = {"apps:read", "apps:deploy", "apps:destroy", "backups:restore", "servers:read"}

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, request.url.path, body or dict(request.url.params)))
        if request.headers.get("authorization") != "Bearer tend_pat_goodtoken":
            return httpx.Response(401, json={"detail": "Invalid API token."})
        p, m = request.url.path, request.method
        if p == "/api/auth/me":
            return httpx.Response(200, json={"user": {"username": "alice"}})
        if p == "/api/health":
            return httpx.Response(200, json={"ok": True})
        if p == "/api/orchestrator/apps" and m == "GET":
            return httpx.Response(200, json=[APP])
        if p == "/api/orchestrator/apps/a1" and m == "GET":
            return httpx.Response(200, json=APP)
        if p == "/api/orchestrator/apps/a1/deletion-impact":
            return httpx.Response(200, json={"volumes": 1})
        if p == "/api/orchestrator/apps/a1" and m == "DELETE":
            if "apps:destroy" not in self.scopes:
                return httpx.Response(403, json={"detail": "API token is missing scope(s): apps:destroy"})
            return httpx.Response(204)
        if p == "/api/orchestrator/apply":
            return httpx.Response(202, json={"run_id": "r9", "ops": 1, "summary": "1 op", "dry_run": body["dry_run"]})
        if p == "/api/orchestrator/apply-runs/r9":
            return httpx.Response(200, json={"run_id": "r9", "status": "succeeded"})
        if p == "/api/orchestrator/apps/a1/backups/restore":
            if body["confirm_name"] != "blog":
                return httpx.Response(400, json={"detail": "confirm_name does not match"})
            return httpx.Response(200, json={"status": "ok"})
        if p == "/api/orchestrator/ssh-keys":
            return httpx.Response(
                403, json={"detail": "API tokens cannot call this endpoint. Sign in to the panel instead."}
            )
        if p == "/api/orchestrator/catalog":
            return httpx.Response(
                200, json=[{"id": "ghost", "name": "Ghost", "description": "Blogging"}, {"id": "n8n", "name": "n8n"}]
            )
        return httpx.Response(404, json={"detail": "not found"})


@pytest.fixture
def panel() -> FakePanel:
    return FakePanel()


@pytest.fixture
def config() -> Config:
    return Config.from_env({"TEND_URL": "https://panel.test", "TEND_TOKEN": "tend_pat_goodtoken"})


@pytest.fixture
async def client(panel: FakePanel, config: Config):
    c = PanelClient(config, transport=httpx.MockTransport(panel.handler))
    yield c
    await c.aclose()


# ---------- config ----------


def test_config_requires_url_and_token():
    with pytest.raises(ConfigError):
        Config.from_env({})
    with pytest.raises(ConfigError):
        Config.from_env({"TEND_URL": "https://p", "TEND_TOKEN": "not-a-tend-token"})
    c = Config.from_env({"TEND_URL": "https://p/", "TEND_TOKEN": "tend_pat_x", "TEND_ALLOW_DESTRUCTIVE": "true"})
    assert c.url == "https://p" and c.allow_destructive and c.verify_tls


# ---------- read ----------


async def test_list_apps_is_compact(client: PanelClient):
    rows = await tools.list_apps(client)
    assert rows == [
        {
            "uuid": "a1",
            "name": "blog",
            "server_uuid": "s1",
            "source": "image",
            "source_ref": "ghost:5",
            "lifecycle": "permanent",
            "auto_deploy": True,
            "tags": [],
            "last_deployed_at": 2,
            "image_update_available": None,
            "expires_at": None,
            "domains": ["blog.example.com"],
        }
    ]
    assert "build" not in rows[0]


async def test_find_app_case_insensitive(client: PanelClient):
    assert (await tools.find_app(client, "BLOG"))["uuid"] == "a1"
    assert await tools.find_app(client, "nope") is None


async def test_search_catalog(client: PanelClient):
    assert [e["id"] for e in await tools.search_catalog(client, "blog")] == ["ghost"]


# ---------- ship ----------


async def test_deploy_returns_run_id_and_poll(client: PanelClient):
    res = await tools.deploy_app(client, "a1")
    assert res["run_id"] == "r9"
    assert (await tools.get_run(client, "r9"))["status"] == "succeeded"


# ---------- danger ----------


async def test_delete_requires_exact_name_locally(client: PanelClient, panel: FakePanel):
    with pytest.raises(ValueError):
        await tools.delete_app(client, "a1", confirm_name="Blog")
    assert not any(m == "DELETE" for m, _, _ in panel.calls)


async def test_delete_with_correct_name(client: PanelClient, panel: FakePanel):
    res = await tools.delete_app(client, "a1", confirm_name="blog", destroy_container=True)
    assert res["deleted"] == "a1" and res["impact"] == {"volumes": 1}
    m, p, params = [c for c in panel.calls if c[0] == "DELETE"][0]
    assert params == {"destroy_container": "true", "delete_volumes": "false"}


async def test_missing_scope_surfaces_panel_message(client: PanelClient, panel: FakePanel):
    panel.scopes.discard("apps:destroy")
    with pytest.raises(PanelError) as exc:
        await tools.delete_app(client, "a1", confirm_name="blog")
    assert exc.value.status == 403 and "apps:destroy" in exc.value.detail and "scope" in exc.value.hint()


async def test_restore_passes_confirm_name(client: PanelClient):
    assert (await tools.restore_backup(client, "a1", "t1", "snap", confirm_name="blog"))["status"] == "ok"
    with pytest.raises(PanelError) as exc:
        await tools.restore_backup(client, "a1", "t1", "snap", confirm_name="wrong")
    assert exc.value.status == 400


async def test_bad_token_hint(panel: FakePanel):
    cfg = Config.from_env({"TEND_URL": "https://panel.test", "TEND_TOKEN": "tend_pat_bad"})
    c = PanelClient(cfg, transport=httpx.MockTransport(panel.handler))
    with pytest.raises(PanelError) as exc:
        await c.me()
    assert exc.value.status == 401 and "revoked or expired" in exc.value.hint()
    await c.aclose()


# ---------- server binding ----------


async def test_server_hides_client_param_and_gates_danger(config: Config, panel: FakePanel):
    c = PanelClient(config, transport=httpx.MockTransport(panel.handler))
    server = build_server(config, client=c)
    listed = {t.name: t for t in await server.list_tools()}
    assert "list_apps" in listed and "deploy_app" in listed
    assert "delete_app" not in listed, "destructive tools must be off by default"
    assert "c" not in listed["get_app"].input_schema["properties"]
    assert listed["get_app"].input_schema["required"] == ["app_uuid"]
    assert listed["list_apps"].annotations.read_only_hint is True

    danger_cfg = Config.from_env({"TEND_URL": config.url, "TEND_TOKEN": config.token, "TEND_ALLOW_DESTRUCTIVE": "1"})
    danger = build_server(danger_cfg, client=c)
    names = {t.name for t in await danger.list_tools()}
    assert {"delete_app", "restore_backup", "docker_cleanup"} <= names

    result = await server.call_tool("find_app", {"name": "blog"})
    payload = result[1] if isinstance(result, tuple) else result
    assert "a1" in json.dumps(payload, default=str)
    await c.aclose()
