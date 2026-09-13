"""Private worker-v1 stdio adapter. No HTTP, credentials, core API or I/O tools.

Core owns authentication, grants, resource filtering and all operations. This
worker describes tools and renders already-authorized, closed-shape projections.
It never treats its own process identity as user authority.
"""

from __future__ import annotations

import json
import re
import sys
from typing import Any, BinaryIO

from . import __version__

MAX_FRAME = 64 * 1024
MAX_ITEMS = 100
CAPABILITIES = {
    "mcp.apps.summary.read": ("apps", {"id", "name", "status"}),
    "mcp.deployments.status.read": ("deployments", {"id", "app_id", "status"}),
    "mcp.servers.health.read": ("servers", {"id", "name", "health"}),
}
_TOKEN = re.compile(r"[A-Za-z0-9_-]{1,64}")
_STATUSES = {"running", "stopped", "starting", "failed", "pending", "succeeded", "unknown", "healthy", "unhealthy"}


class InvalidRequest(ValueError):
    pass


def unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise InvalidRequest
        result[key] = value
    return result


def reject_constant(value: str) -> None:
    raise InvalidRequest


def encode(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")


def tools() -> list[dict[str, Any]]:
    return [
        {
            "name": capability,
            "description": f"Read authorized {kind} summaries from Tend.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
        }
        for capability, (kind, _) in sorted(CAPABILITIES.items())
    ]


def render(capability: object, result: object) -> dict[str, Any]:
    if not isinstance(capability, str) or capability not in CAPABILITIES:
        raise InvalidRequest
    kind, fields = CAPABILITIES[capability]
    if not isinstance(result, dict) or set(result) != {"items", "has_more"} or type(result["has_more"]) is not bool:
        raise InvalidRequest
    items = result["items"]
    if not isinstance(items, list) or len(items) > MAX_ITEMS:
        raise InvalidRequest
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict) or set(item) != fields:
            raise InvalidRequest
        for key, value in item.items():
            if (
                not isinstance(value, str)
                or not 1 <= len(value) <= 128
                or any(ord(c) < 32 or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF for c in value)
            ):
                raise InvalidRequest
            if key in ("id", "app_id") and not _TOKEN.fullmatch(value):
                raise InvalidRequest
            if key in ("status", "health") and value not in _STATUSES:
                raise InvalidRequest
        if item["id"] in seen:
            raise InvalidRequest
        seen.add(item["id"])
    data = {kind: items, "has_more": result["has_more"]}
    # MCP text content represents the same strict projection. Resource names
    # remain untrusted data, not instructions to the consuming coding agent.
    return {"content": [{"type": "text", "text": encode(data).decode("ascii")}], "structuredContent": data, "isError": False}


def handle(frame: bytes) -> bytes:
    request_id: str | None = None
    try:
        if not 1 <= len(frame) <= MAX_FRAME:
            raise InvalidRequest
        request = json.loads(frame.decode("utf-8"), object_pairs_hook=unique_pairs, parse_constant=reject_constant)
        if not isinstance(request, dict):
            raise InvalidRequest
        candidate = request.get("request_id")
        if not isinstance(candidate, str) or not _TOKEN.fullmatch(candidate):
            raise InvalidRequest
        request_id = candidate
        if type(request.get("protocol")) is not int or request["protocol"] != 1:
            raise InvalidRequest
        operation = request.get("operation")
        fields = {"protocol", "request_id", "operation"}
        if operation == "render":
            fields |= {"capability", "result"}
        if set(request) != fields:
            raise InvalidRequest
        if operation == "hello":
            result: object = {
                "component_id": "host.tend.mcp",
                "version": __version__,
                "protocol": 1,
                "capabilities": sorted(CAPABILITIES),
            }
        elif operation == "tools.list":
            result = {"tools": tools()}
        elif operation == "render":
            result = render(request["capability"], request["result"])
        else:
            raise InvalidRequest
        response = encode({"protocol": 1, "request_id": request_id, "result": result})
        if len(response) > MAX_FRAME:
            raise InvalidRequest
        return response
    except (ValueError, UnicodeError, RecursionError):
        return encode({"protocol": 1, "request_id": request_id, "error": "invalid_request"})


def run(source: BinaryIO, destination: BinaryIO) -> int:
    while True:
        frame = source.readline(MAX_FRAME + 1)
        if not frame:
            return 0
        if len(frame) > MAX_FRAME or not frame.endswith(b"\n"):
            destination.write(encode({"protocol": 1, "request_id": None, "error": "invalid_frame"}) + b"\n")
            destination.flush()
            return 1
        destination.write(handle(frame) + b"\n")
        destination.flush()


def main() -> None:
    try:
        if sys.argv[1:] == ["--probe"]:
            sys.stdout.buffer.write(handle(b'{"protocol":1,"request_id":"probe","operation":"hello"}') + b"\n")
            status = 0
        elif sys.argv[1:]:
            status = 1
        else:
            status = run(sys.stdin.buffer, sys.stdout.buffer)
    except (OSError, ValueError):
        status = 1  # No traceback, path, environment or raw request in stderr.
    raise SystemExit(status)


if __name__ == "__main__":
    main()
