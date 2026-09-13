"""`tend-mcp` entry point.

tend-mcp            run the MCP server on stdio (what agents launch)
tend-mcp doctor     check URL, token, scopes and panel reachability
tend-mcp http       reserved; blocked until authenticated component ingress exists
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from . import __version__
from .client import PanelClient, PanelError
from .config import Config, ConfigError


def _load_config() -> Config:
    try:
        return Config.from_env()
    except ConfigError as exc:
        print(f"tend-mcp: {exc}", file=sys.stderr)
        sys.exit(2)


async def _doctor(config: Config) -> int:
    client = PanelClient(config)
    ok = True
    try:
        print(f"tend-mcp {__version__}")
        print(f"panel   {config.url}  (tls verify: {'on' if config.verify_tls else 'OFF'})")
        try:
            health = await client.health()
            print(f"health  ok  {health if isinstance(health, dict) else ''}")
        except PanelError as exc:
            ok = False
            print(f"health  FAIL  {exc.detail}")
        try:
            me = await client.me()
            user = me.get("user") or me
            print(f"token   ok  acting as '{user.get('username', '?')}'")
        except PanelError as exc:
            ok = False
            print(f"token   FAIL  {exc.detail}  {exc.hint()}")
        try:
            apps = await client.get("/api/orchestrator/apps")
            print(f"scopes  apps:read ok  ({len(apps)} apps)")
        except PanelError as exc:
            print(f"scopes  apps:read missing  {exc.detail}")
        print(f"danger  {'ENABLED (TEND_ALLOW_DESTRUCTIVE)' if config.allow_destructive else 'disabled'}")
    finally:
        await client.aclose()
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="tend-mcp", description="MCP server for tend.host")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("doctor", help="verify connectivity, token and scopes")
    http = sub.add_parser("http", help="reserved for authenticated component ingress (currently disabled)")
    http.add_argument("--host", default="127.0.0.1")
    http.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)

    if args.cmd == "http":
        parser.error(
            "HTTP serving is disabled until core-authenticated component ingress is implemented. "
            "A shared panel token does not authenticate MCP clients. Use local stdio for prototype testing."
        )

    config = _load_config()
    if args.cmd == "doctor":
        sys.exit(asyncio.run(_doctor(config)))

    from .server import build_server  # lazy: keeps `doctor` fast

    server = build_server(config)
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
