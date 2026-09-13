# TEND MCP

> **Development prototype — component implementation pending.** TEND MCP will
> install and update independently in Tend's existing component catalog, like
> Notes and Sites. See [ROADMAP.md](ROADMAP.md) for the canonical direction and
> release gates. The standalone usage below is historical prototype guidance,
> not a supported component installation. Do not expose its HTTP service publicly.

MCP server that lets coding agents (Claude Code, Codex, Cursor, pi, Zed, …)
operate a [tend.host](https://github.com/wilkinsantana/tend.host) panel —
deploy, roll back, inspect logs and health, run backups — **without SSH and
without a browser login**.

It talks to the panel's normal HTTPS API with a scoped personal API token.
Everything an agent does shows up in the panel's audit log under that token's
name, goes through the same reconciler as the UI, and respects the same named
confirmations for destructive actions.

## Requirements

- tend.host with API tokens (Settings → Account → API tokens).
- Python 3.11+ and [`uv`](https://docs.astral.sh/uv/) (for `uvx`).

## 1. Create a token in tend.host

Settings → Account → **API tokens** → *New token*. The **Agent default**
preset gives read + deploy + exec + backups:run; it deliberately excludes
`apps:destroy`, `servers:destroy` and `backups:restore`. Copy the token — it
is shown once.

## 2. Add the server to your agent

Environment:

| Variable | Required | Meaning |
|---|---|---|
| `TEND_URL` | yes | Panel base URL, e.g. `https://panel.example.com` |
| `TEND_TOKEN` | yes | `tend_pat_…` token |
| `TEND_ALLOW_DESTRUCTIVE` | no | `1` to expose `delete_app`, `restore_backup`, `docker_cleanup` (token must also hold the scope) |
| `TEND_INSECURE_TLS` | no | `1` to skip TLS verification for a self-signed local panel |
| `TEND_TIMEOUT_SECONDS` | no | HTTP timeout (default 60) |

**Claude Code**

```bash
claude mcp add tend -e TEND_URL=https://panel.example.com -e TEND_TOKEN=tend_pat_… -- uvx tend-mcp
```

**Codex CLI** (`~/.codex/config.toml`)

```toml
[mcp_servers.tend]
command = "uvx"
args = ["tend-mcp"]
env = { TEND_URL = "https://panel.example.com", TEND_TOKEN = "tend_pat_…" }
```

**Cursor / Zed / generic** (`mcp.json`)

```json
{
  "mcpServers": {
    "tend": {
      "command": "uvx",
      "args": ["tend-mcp"],
      "env": { "TEND_URL": "https://panel.example.com", "TEND_TOKEN": "tend_pat_…" }
    }
  }
}
```

**pi** — `pi install npm:…` is not needed; add the same command under
`mcpServers` in `~/.pi/agent/settings.json`.

## 3. Check it

```bash
TEND_URL=… TEND_TOKEN=… uvx tend-mcp doctor
```

## Tools

Read (`apps:read`, `servers:read`, `backups:read`, `catalog:read`, `audit:read`):
`list_apps`, `get_app`, `find_app`, `get_status`, `list_deploys`, `get_run`,
`list_app_runs`, `get_app_stats`, `list_servers`, `verify_server`,
`server_docker_usage`, `list_backup_runs`, `list_snapshots`,
`list_backup_targets`, `search_catalog`, `recent_audit`, `firing_alerts`,
`list_tasks`.

Ship (`apps:deploy`, `apps:write`, `apps:exec`, `domains:write`, `backups:run`):
`deploy_app`, `deploy_all`, `restart_app`, `rollback_app`, `check_updates`,
`update_app`, `add_domain`, `remove_domain`, `run_backup`, `run_task`.

Danger (only with `TEND_ALLOW_DESTRUCTIVE=1`; `apps:destroy`, `servers:destroy`,
`backups:restore`): `delete_app(confirm_name)`, `restore_backup(confirm_name)`,
`docker_cleanup`.

Deploys and rollbacks return a `run_id`; agents poll `get_run` until the run
is terminal. The `tend://guide` resource and `diagnose_app` prompt teach an
agent the expected order of operations.

## Security model

- Tokens are user-owned, scope-limited, revocable, and hash-only on the panel.
- A token can never create tokens, change credentials, or reach
  browser-only endpoints (SSH keys, provider config, secrets). The panel
  denies tokens on any endpoint not explicitly allow-listed.
- Destructive actions require the exact resource name; there is no
  `force=true`.
- Never commit `TEND_TOKEN`. Rotate it from Settings → API tokens.

## Development

```bash
uv sync
uv run pytest -q
uv run ruff check src tests && uv run mypy src
TEND_URL=https://panel.local TEND_TOKEN=tend_pat_… uv run tend-mcp doctor
```

Tests run against an in-process fake panel; no real tend.host is needed.

## Compatibility

| tend-mcp | tend.host |
|---|---|
| 0.1.x | API tokens + route policy (feature branch `feat/agent-api-tokens`, 2026-09) |

## License

MIT.
