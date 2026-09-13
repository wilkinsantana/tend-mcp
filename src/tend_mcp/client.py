"""Thin async HTTP client for the tend.host panel API.

Every call sends the bearer token. Panel errors are surfaced as ``PanelError``
with the HTTP status and the panel's own ``detail`` message so agents see the
same guidance a human would (missing scope, named-confirmation mismatch, …).
"""

from __future__ import annotations

from typing import Any

import httpx

from . import __version__
from .config import Config


class PanelError(RuntimeError):
    def __init__(self, status: int, detail: str, *, method: str, path: str):
        self.status = status
        self.detail = detail
        self.method = method
        self.path = path
        super().__init__(f"{method} {path} -> {status}: {detail}")

    def hint(self) -> str:
        if self.status == 401:
            return "Token rejected. It may be revoked or expired; create a new one in Settings → API tokens."
        if self.status == 403 and "scope" in self.detail:
            return "The token lacks a required scope. Ask the panel owner to issue a token with that scope."
        if self.status == 403:
            return "This endpoint is browser-only; a human must do it in the panel UI."
        if self.status == 429:
            return "Rate limited by the panel. Wait a minute and retry."
        return ""


class PanelClient:
    def __init__(self, config: Config, transport: httpx.AsyncBaseTransport | None = None):
        self._config = config
        self._http = httpx.AsyncClient(
            base_url=config.url,
            headers={
                "Authorization": f"Bearer {config.token}",
                "User-Agent": f"tend-mcp/{__version__}",
                "Accept": "application/json",
            },
            timeout=config.timeout_seconds,
            verify=config.verify_tls,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
    ) -> Any:
        try:
            r = await self._http.request(method, path, params=params, json=json)
        except httpx.ConnectError as exc:
            raise PanelError(0, f"Cannot reach panel at {self._config.url}: {exc}", method=method, path=path) from exc
        except httpx.TimeoutException as exc:
            raise PanelError(0, f"Panel timed out after {self._config.timeout_seconds}s", method=method, path=path) from exc
        if r.status_code >= 400:
            detail: Any
            try:
                body = r.json()
                detail = body.get("detail") if isinstance(body, dict) else str(body)
                if isinstance(detail, list):  # pydantic validation errors
                    detail = "; ".join(f"{'.'.join(map(str, e.get('loc', [])))}: {e.get('msg')}" for e in detail)
            except ValueError:
                detail = r.text[:500]
            raise PanelError(r.status_code, str(detail or r.reason_phrase), method=method, path=path)
        if r.status_code == 204 or not r.content:
            return None
        return r.json()

    async def get(self, path: str, **params: Any) -> Any:
        return await self.request("GET", path, params={k: v for k, v in params.items() if v is not None})

    async def post(self, path: str, json: Any = None, **params: Any) -> Any:
        return await self.request("POST", path, json=json, params={k: v for k, v in params.items() if v is not None})

    async def patch(self, path: str, json: Any = None) -> Any:
        return await self.request("PATCH", path, json=json)

    async def delete(self, path: str, **params: Any) -> Any:
        return await self.request("DELETE", path, params={k: v for k, v in params.items() if v is not None})

    # ---------- identity ----------

    async def me(self) -> dict[str, Any]:
        return await self.get("/api/auth/me")

    async def health(self) -> dict[str, Any]:
        return await self.get("/api/health")
