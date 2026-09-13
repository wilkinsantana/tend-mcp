"""Curated, intent-level tools over the tend.host API.

Each tool is a plain async function taking a ``PanelClient`` so it can be unit
tested without MCP transport. ``server.py`` binds them to MCP with the same
names and docstrings. Keep the set small; agents reason better about ~25
intents than 500 routes.

Scope conventions (must match the panel's ``core/scopes.py``):
  read   -> apps:read / servers:read / backups:read / catalog:read / audit:read
  ship   -> apps:deploy / apps:write / apps:exec / domains:write / backups:run
  danger -> apps:destroy / servers:destroy / backups:restore  (gated twice:
            TEND_ALLOW_DESTRUCTIVE=1 *and* the token holds the scope; the panel
            still requires the resource name as confirmation)
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from .client import PanelClient

ORCH = "/api/orchestrator"


def _slim_app(a: dict[str, Any]) -> dict[str, Any]:
    """Drop bulky config blobs from list views; get_app returns everything."""
    return {
        k: a.get(k)
        for k in (
            "uuid",
            "name",
            "server_uuid",
            "source",
            "source_ref",
            "lifecycle",
            "auto_deploy",
            "tags",
            "last_deployed_at",
            "image_update_available",
            "expires_at",
        )
    } | {"domains": [d.get("host") for d in a.get("domains") or [] if isinstance(d, dict)]}


# ======================================================================
# read
# ======================================================================


async def list_apps(c: PanelClient, server_uuid: str | None = None) -> list[dict[str, Any]]:
    """List apps managed by the panel (optionally on one server). Compact rows; use get_app for detail."""
    return [_slim_app(a) for a in await c.get(f"{ORCH}/apps", server_uuid=server_uuid)]


async def get_app(c: PanelClient, app_uuid: str) -> dict[str, Any]:
    """Full app record: source, build/runtime config, env set names, volumes, domains, update status."""
    return await c.get(f"{ORCH}/apps/{app_uuid}")


async def get_status(c: PanelClient) -> Any:
    """Live container state for every app on every server (running / exited / missing)."""
    return await c.get(f"{ORCH}/status")


async def find_app(c: PanelClient, name: str) -> dict[str, Any] | None:
    """Resolve an app by exact name (case-insensitive). Returns None when not found."""
    wanted = name.strip().lower()
    for a in await c.get(f"{ORCH}/apps"):
        if str(a.get("name", "")).lower() == wanted:
            return _slim_app(a)
    return None


async def list_deploys(c: PanelClient, app_uuid: str, limit: int = 10) -> list[dict[str, Any]]:
    """Deploy history for an app, newest first. Each row has deploy id, image ref, source revision, status."""
    return await c.get(f"{ORCH}/apps/{app_uuid}/deploys", limit=limit)


async def get_run(c: PanelClient, run_id: str) -> dict[str, Any]:
    """Progress of an apply/deploy run returned by deploy_app, redeploy_app or rollback_app. Poll until finished."""
    return await c.get(f"{ORCH}/apply-runs/{run_id}")


async def list_app_runs(c: PanelClient, app_uuid: str) -> Any:
    """Recent apply runs for an app (ids, status, timestamps)."""
    return await c.get(f"{ORCH}/apps/{app_uuid}/apply-runs")


async def get_app_stats(c: PanelClient, app_uuid: str) -> Any:
    """CPU / memory / network snapshot for an app container."""
    return await c.get(f"{ORCH}/apps/{app_uuid}/stats")


async def list_servers(c: PanelClient) -> list[dict[str, Any]]:
    """Servers the panel manages. Secrets are never included."""
    rows = await c.get(f"{ORCH}/servers")
    return [{k: s.get(k) for k in ("uuid", "name", "host", "port", "user", "role", "public_ip", "created_at")} for s in rows]


async def verify_server(c: PanelClient, server_uuid: str) -> Any:
    """Non-destructive health probe: SSH marker, stats source, Docker ping."""
    return await c.post(f"{ORCH}/servers/{server_uuid}/verify")


async def server_docker_usage(c: PanelClient, server_uuid: str) -> Any:
    """Docker disk usage on a server (images, containers, volumes, build cache)."""
    return await c.get(f"{ORCH}/servers/{server_uuid}/docker-df")


async def list_backup_runs(c: PanelClient, app_uuid: str | None = None) -> Any:
    """Backup run history, newest first; optionally for one app."""
    return await c.get(f"{ORCH}/apps/{app_uuid}/backups/runs" if app_uuid else f"{ORCH}/backups/runs")


async def list_snapshots(c: PanelClient, app_uuid: str) -> Any:
    """Restic snapshots available for an app (ids usable by restore_backup)."""
    return await c.get(f"{ORCH}/apps/{app_uuid}/backups/snapshots")


async def list_backup_targets(c: PanelClient) -> Any:
    """Configured backup destinations (target ids for run_backup)."""
    return await c.get(f"{ORCH}/backups/targets")


async def search_catalog(c: PanelClient, query: str = "") -> list[dict[str, Any]]:
    """Search the one-click app catalog by name/description substring."""
    catalog = await c.get(f"{ORCH}/catalog")
    entries = catalog if isinstance(catalog, list) else catalog.get("entries", catalog.get("apps", []))
    q = query.strip().lower()
    out = []
    for e in entries:
        text = " ".join(str(e.get(k, "")) for k in ("id", "slug", "name", "title", "description", "summary")).lower()
        if not q or q in text:
            out.append({k: e.get(k) for k in ("id", "slug", "name", "title", "description", "summary", "image") if k in e})
    return out[:50]


async def recent_audit(c: PanelClient, limit: int = 30) -> Any:
    """Panel activity feed (who did what, including agents acting via tokens)."""
    return await c.get(f"{ORCH}/audit", limit=limit)


async def firing_alerts(c: PanelClient) -> Any:
    """Alert rules currently firing across apps."""
    return await c.get(f"{ORCH}/alerts/firing")


# ======================================================================
# ship
# ======================================================================


async def deploy_app(c: PanelClient, app_uuid: str, dry_run: bool = False) -> dict[str, Any]:
    """Reconcile an app to its desired state (build/pull + replace container). Returns run_id; poll get_run."""
    return await c.post(f"{ORCH}/apply", json={"app_uuids": [app_uuid], "dry_run": dry_run})


async def deploy_all(c: PanelClient, dry_run: bool = False) -> dict[str, Any]:
    """Reconcile every app. Prefer deploy_app for one app. Returns run_id."""
    return await c.post(f"{ORCH}/apply", json={"app_uuids": [], "dry_run": dry_run})


async def restart_app(c: PanelClient, app_uuid: str) -> Any:
    """Restart the app container without rebuilding."""
    return await c.post(f"{ORCH}/apps/{app_uuid}/restart")


async def rollback_app(c: PanelClient, app_uuid: str, deploy_id: int) -> Any:
    """Roll back to a previous deploy (id from list_deploys). Starts a guarded apply."""
    return await c.post(f"{ORCH}/apps/{app_uuid}/rollback", json={"deploy_id": deploy_id})


async def check_updates(c: PanelClient, app_uuid: str) -> Any:
    """Check whether a newer image/commit is available for the app (no changes made)."""
    return await c.post(f"{ORCH}/apps/{app_uuid}/check-updates")


async def update_app(c: PanelClient, app_uuid: str, patch: dict[str, Any]) -> Any:
    """Patch app configuration (e.g. source_ref, auto_deploy, tags, runtime). Follow with deploy_app to apply."""
    return await c.patch(f"{ORCH}/apps/{app_uuid}", json=patch)


async def add_domain(c: PanelClient, app_uuid: str, host: str, provision_dns: bool = False) -> Any:
    """Attach a domain to an app (Caddy + TLS). Optionally create the DNS record via Cloudflare."""
    result = await c.post(f"{ORCH}/apps/{app_uuid}/domains", json={"host": host})
    if provision_dns:
        result = {"domain": result, "dns": await c.post(f"{ORCH}/apps/{app_uuid}/domains/{host}/provision-dns")}
    return result


async def remove_domain(c: PanelClient, app_uuid: str, host: str) -> Any:
    """Detach a domain from an app."""
    return await c.delete(f"{ORCH}/apps/{app_uuid}/domains/{host}")


async def run_backup(c: PanelClient, app_uuid: str, target_id: str) -> Any:
    """Run an on-demand backup of the app's volumes to a target (ids from list_backup_targets). Blocks until done."""
    return await c.post(f"{ORCH}/apps/{app_uuid}/backups/run", json={"target_id": target_id})


async def run_task(c: PanelClient, app_uuid: str, task_id: str) -> Any:
    """Run a scheduled task (docker exec) now. Task ids from list_tasks."""
    return await c.post(f"{ORCH}/apps/{app_uuid}/tasks/{task_id}/run")


async def list_tasks(c: PanelClient, app_uuid: str) -> Any:
    """Scheduled docker-exec tasks configured for an app."""
    return await c.get(f"{ORCH}/apps/{app_uuid}/tasks")


# ======================================================================
# danger — panel enforces named confirmation; server enforces scopes
# ======================================================================


async def delete_app(
    c: PanelClient,
    app_uuid: str,
    confirm_name: str,
    destroy_container: bool = False,
    delete_volumes: bool = False,
) -> dict[str, Any]:
    """Remove an app from the panel. confirm_name must equal the app's name.
    destroy_container=True also stops/removes the container; delete_volumes=True wipes its data volumes."""
    app = await c.get(f"{ORCH}/apps/{app_uuid}")
    if confirm_name.strip() != app.get("name"):
        raise ValueError(f"confirm_name must be exactly '{app.get('name')}' to delete this app.")
    impact = await c.get(f"{ORCH}/apps/{app_uuid}/deletion-impact")
    await c.delete(
        f"{ORCH}/apps/{app_uuid}",
        destroy_container=destroy_container,
        delete_volumes=delete_volumes,
    )
    return {"deleted": app_uuid, "name": app.get("name"), "impact": impact}


async def restore_backup(
    c: PanelClient, app_uuid: str, target_id: str, snapshot_id: str, confirm_name: str, safety_backup: bool = True
) -> Any:
    """Restore an app's volumes from a snapshot. OVERWRITES live data. confirm_name must equal the app name."""
    return await c.post(
        f"{ORCH}/apps/{app_uuid}/backups/restore",
        json={
            "target_id": target_id,
            "snapshot_id": snapshot_id,
            "confirm_name": confirm_name,
            "safety_backup": safety_backup,
        },
    )


async def docker_cleanup(c: PanelClient, server_uuid: str) -> Any:
    """Prune unreferenced images/build cache on a server. Never touches volumes."""
    return await c.post(f"{ORCH}/servers/{server_uuid}/docker-cleanup")


READ_TOOLS: list[Callable[..., Awaitable[Any]]] = [
    list_apps,
    get_app,
    find_app,
    get_status,
    list_deploys,
    get_run,
    list_app_runs,
    get_app_stats,
    list_servers,
    verify_server,
    server_docker_usage,
    list_backup_runs,
    list_snapshots,
    list_backup_targets,
    search_catalog,
    recent_audit,
    firing_alerts,
    list_tasks,
]
SHIP_TOOLS: list[Callable[..., Awaitable[Any]]] = [
    deploy_app,
    deploy_all,
    restart_app,
    rollback_app,
    check_updates,
    update_app,
    add_domain,
    remove_domain,
    run_backup,
    run_task,
]
DANGER_TOOLS: list[Callable[..., Awaitable[Any]]] = [delete_app, restore_backup, docker_cleanup]
