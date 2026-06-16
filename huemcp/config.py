"""Configuration loaded from environment variables.

In production the launchd plist supplies HUE_* vars; for local dev a gitignored .env
is fine. The bridge IP + app key may come from env (HUE_BRIDGE_IP / HUE_APP_KEY) or,
if absent, from the pairing state file written by `huemcp-cli pair` (state.json).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

_DEFAULT_STATE_DIR = Path.home() / ".hueMCP"


@dataclass
class Config:
    auth_token: str
    bind: str = "0.0.0.0"
    port: int = 8910
    tls_mode: str = "selfsigned"  # "selfsigned" | "none"
    state_dir: Path = _DEFAULT_STATE_DIR
    log_level: str = "info"
    # Hue bridge connection (optional in env; falls back to state.json from pairing).
    bridge_ip: str | None = None
    app_key: str | None = None
    # TTL (seconds) for the cached structural lookups (room->grouped_light, light ids)
    # used on the write path. State (on/brightness) is always read live.
    cache_ttl_s: float = 600.0

    @classmethod
    def from_env(cls, env: dict | None = None) -> "Config":
        e = env if env is not None else os.environ
        token = e.get("HUE_AUTH_TOKEN", "")
        if not token:
            raise RuntimeError(
                "HUE_AUTH_TOKEN is required. Generate one with: openssl rand -hex 32"
            )
        return cls(
            auth_token=token,
            bind=e.get("HUE_BIND", "0.0.0.0"),
            port=int(e.get("HUE_PORT", "8910")),
            tls_mode=e.get("HUE_TLS", "selfsigned"),
            state_dir=Path(e.get("HUE_STATE_DIR", str(_DEFAULT_STATE_DIR))),
            log_level=e.get("HUE_LOG_LEVEL", "info"),
            bridge_ip=e.get("HUE_BRIDGE_IP") or None,
            app_key=e.get("HUE_APP_KEY") or None,
            cache_ttl_s=float(e.get("HUE_CACHE_TTL_S", "600")),
        )

    @property
    def cert_path(self) -> Path:
        return self.state_dir / "cert.pem"

    @property
    def key_path(self) -> Path:
        return self.state_dir / "key.pem"

    @property
    def state_path(self) -> Path:
        return self.state_dir / "state.json"
