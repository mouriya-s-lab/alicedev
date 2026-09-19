"""Environment-backed configuration for the gateway process."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Mapping
from urllib.parse import urlsplit


class ConfigError(ValueError):
    """Raised when a required gateway setting is missing or malformed."""


@dataclass(frozen=True, slots=True)
class GatewayConfig:
    """Immutable process configuration shared by the gateway handlers."""

    secret: bytes
    internal_token: str
    paseo_upstream: str
    paseo_password: str
    bot_upstream: str
    public_host: str
    reports_published_root: Path
    listen_host: str = "0.0.0.0"
    listen_port: int = 8080

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "GatewayConfig":
        env = os.environ if environ is None else environ

        gateway_secret = _required(env, "GATEWAY_SECRET")
        internal_token = _required(env, "ALICEDEV_INTERNAL_TOKEN")
        paseo_password = _required(env, "PASEO_PASSWORD")
        public_host = _required(env, "PUBLIC_HOST").strip()
        reports_root_value = _required(env, "REPORTS_PUBLISHED_ROOT")

        if not public_host or "/" in public_host or "\\" in public_host:
            raise ConfigError("PUBLIC_HOST must be a host name, optionally with a port")
        if any(char.isspace() for char in public_host):
            raise ConfigError("PUBLIC_HOST must not contain whitespace")

        paseo_upstream = env.get("PASEO_UPSTREAM", "http://paseo:6767").strip()
        bot_upstream = env.get("BOT_UPSTREAM", "http://astrbot:6200").strip()
        _validate_upstream("PASEO_UPSTREAM", paseo_upstream)
        _validate_upstream("BOT_UPSTREAM", bot_upstream)

        reports_root = Path(reports_root_value).expanduser()
        if not reports_root.is_absolute():
            raise ConfigError("REPORTS_PUBLISHED_ROOT must be an absolute path")

        return cls(
            secret=gateway_secret.encode("utf-8"),
            internal_token=internal_token,
            paseo_upstream=paseo_upstream.rstrip("/"),
            paseo_password=paseo_password,
            bot_upstream=bot_upstream.rstrip("/"),
            public_host=public_host,
            reports_published_root=reports_root,
        )


def _required(environ: Mapping[str, str], name: str) -> str:
    value = environ.get(name, "").strip()
    if not value:
        raise ConfigError(f"{name} must be non-empty")
    return value


def _validate_upstream(name: str, value: str) -> None:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ConfigError(f"{name} must be an http(s) URL")
    if parsed.username is not None or parsed.password is not None:
        raise ConfigError(f"{name} must not contain credentials")
    if parsed.query or parsed.fragment:
        raise ConfigError(f"{name} must not contain query or fragment components")
