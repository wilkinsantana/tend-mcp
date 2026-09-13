"""Bind the curated tools to an MCP server.

The ``PanelClient`` parameter is stripped from every tool signature before
registration so agents only see the intent-level arguments. Destructive tools
are registered only when ``TEND_ALLOW_DESTRUCTIVE=1``; the panel still
enforces scopes and named confirmation regardless.
"""

from __future__ import annotations

import functools
import inspect
from collections.abc import Callable
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from . import __version__, tools
from .client import PanelClient, PanelError
from .config import Config

GUIDE = """# Operating a tend.host panel

tend.host is a self-hosted control panel that runs apps (Git, Docker image or
Compose) on the owner's servers via SSH + Docker. SQLite on the panel is the
source of truth; a desired-state reconciler ("apply") converges servers.

## How to work
1. Orient: `list_servers`, `list_apps`, `get_status`. Resolve names with `find_app`.
2. Inspect before changing: `get_app`, `list_deploys`, `get_app_stats`, `recent_audit`.
3. Change config with `update_app`, then converge with `deploy_app` — it returns a
   `run_id`; poll `get_run(run_id)` until `status` is terminal. Never assume a
   deploy finished because the call returned.
4. Something broke after a deploy? `list_deploys` → `rollback_app(deploy_id)`.
5. Before risky data work: `list_backup_targets` → `run_backup`.

## Rules the panel enforces (you cannot bypass them)
- Your token has scopes. A 403 naming a scope means the owner must issue a
  token with it; do not look for workarounds.
- Browser-only endpoints (credentials, SSH keys, provider config, secrets)
  are never reachable with a token. Tell the user to do those in the UI.
- Destructive tools (`delete_app`, `restore_backup`, `docker_cleanup`) exist
  only when the server was started with TEND_ALLOW_DESTRUCTIVE=1 and require
  the exact resource name as `confirm_name`. Ask the human first, always.
- Everything you do is written to the panel's audit log under the token's name.

## Do not
- Do not SSH to servers or run docker commands directly; it desyncs the panel.
- Do not retry a 429 in a tight loop; wait ~60s.
"""


def _bind(fn: Callable[..., Any], get_client: Callable[[], PanelClient]) -> Callable[..., Any]:
    """Return a wrapper without the leading PanelClient parameter."""
    sig = inspect.signature(fn)
    params = list(sig.parameters.values())[1:]

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return await fn(get_client(), *args, **kwargs)
        except PanelError as exc:
            hint = exc.hint()
            raise RuntimeError(f"{exc.detail} (HTTP {exc.status}){' — ' + hint if hint else ''}") from exc

    wrapper.__signature__ = sig.replace(parameters=params)  # type: ignore[attr-defined]
    return wrapper


def build_server(config: Config, client: PanelClient | None = None) -> MCPServer:
    _client = client or PanelClient(config)

    def get_client() -> PanelClient:
        return _client

    server = MCPServer(
        name="tend",
        title="tend.host",
        version=__version__,
        instructions=GUIDE,
    )

    read_ann = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True)
    ship_ann = ToolAnnotations(read_only_hint=False, destructive_hint=False)
    danger_ann = ToolAnnotations(read_only_hint=False, destructive_hint=True)

    for fn in tools.READ_TOOLS:
        server.add_tool(_bind(fn, get_client), annotations=read_ann)
    for fn in tools.SHIP_TOOLS:
        server.add_tool(_bind(fn, get_client), annotations=ship_ann)
    if config.allow_destructive:
        for fn in tools.DANGER_TOOLS:
            server.add_tool(_bind(fn, get_client), annotations=danger_ann)

    @server.resource("tend://guide", name="tend.host operating guide", mime_type="text/markdown")
    def guide() -> str:
        return GUIDE

    @server.resource("tend://app/{app_uuid}", name="App snapshot", mime_type="application/json")
    async def app_snapshot(app_uuid: str) -> Any:
        return await tools.get_app(get_client(), app_uuid)

    @server.prompt(name="diagnose_app", description="Investigate why an app is unhealthy and propose a fix.")
    def diagnose_app(app_name: str) -> str:
        return (
            f"Diagnose the tend.host app named '{app_name}'. Steps: find_app → get_app → get_status → "
            "list_deploys → get_app_stats → recent_audit. Summarise the likely cause, then propose the "
            "smallest safe action (restart_app, rollback_app to a specific deploy_id, or a config change via "
            "update_app + deploy_app). Ask before doing anything destructive."
        )

    return server
