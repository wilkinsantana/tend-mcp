"""Environment-driven configuration. No config files: agents run this as a
subprocess and environment variables are the only portable channel."""

from __future__ import annotations

import os
from dataclasses import dataclass

TOKEN_PREFIX = "tend_pat_"


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Config:
    url: str
    token: str
    allow_destructive: bool = False
    verify_tls: bool = True
    timeout_seconds: float = 60.0

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Config:
        e = os.environ if env is None else env
        url = (e.get("TEND_URL") or "").strip().rstrip("/")
        token = (e.get("TEND_TOKEN") or "").strip()
        if not url:
            raise ConfigError("TEND_URL is not set (e.g. https://panel.example.com).")
        if not url.startswith(("http://", "https://")):
            raise ConfigError("TEND_URL must start with http:// or https://.")
        if not token:
            raise ConfigError("TEND_TOKEN is not set. Create one in tend.host → Settings → Account → API tokens.")
        if not token.startswith(TOKEN_PREFIX):
            raise ConfigError(f"TEND_TOKEN does not look like a tend.host API token (expected {TOKEN_PREFIX}…).")
        return cls(
            url=url,
            token=token,
            allow_destructive=_truthy(e.get("TEND_ALLOW_DESTRUCTIVE")),
            verify_tls=not _truthy(e.get("TEND_INSECURE_TLS")),
            timeout_seconds=float(e.get("TEND_TIMEOUT_SECONDS") or 60),
        )


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}
