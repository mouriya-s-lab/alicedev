"""Plugin configuration domain type.

The AstrBot dashboard writes ``data/config/alicedev_config.json`` from
``_conf_schema.json``; :meth:`PluginConfig.from_astrbot` parses that dict-like
config into a frozen dataclass so no dict-shaped config leaks across modules.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


def _as_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (list, tuple)):
        return tuple(str(v) for v in value if str(v).strip())
    if isinstance(value, str):
        return tuple(part for part in (p.strip() for p in value.split(",")) if part)
    return ()


@dataclass(frozen=True)
class PluginConfig:
    """Parsed, validated plugin configuration."""

    paseo_url: str
    paseo_password: str
    internal_token: str
    gateway_url: str
    public_base_url: str
    reports_root: Path
    templates_root: Path
    images_root: Path
    stickers_root: Path
    allowed_chats: tuple[str, ...] = ()
    admin_users: tuple[str, ...] = ()
    github_token: str | None = None
    default_repo: str = "TraderAlice/OpenAlice"
    paseo_provider: str = "omp-alicedev"
    paseo_model: str = ""
    paseo_thinking: str = "high"
    paseo_cwd: str = "/workspace/alicedev"
    internal_api_host: str = "0.0.0.0"
    internal_api_port: int = 6200
    idle_close_seconds: int = 12 * 3600
    sweeper_interval_seconds: int = 600
    inject_wait_max_seconds: int = 600
    inject_poll_seconds: float = 2.0

    def is_admin(self, user_key: str) -> bool:
        return user_key in self.admin_users

    def chat_allowed(self, chat_key: str) -> bool:
        return not self.allowed_chats or chat_key in self.allowed_chats

    @classmethod
    def from_astrbot(cls, config: Mapping[str, Any], *, plugin_dir: Path) -> "PluginConfig":
        """Build from AstrBot's ``AstrBotConfig`` (dict-like) plus the plugin dir.

        Template/card/sticker/image roots default to directories bundled next to
        the plugin so the plugin is self-contained at runtime; every value may be
        overridden through the dashboard config.
        """

        def get(key: str, default: Any = None) -> Any:
            try:
                return config.get(key, default)  # type: ignore[union-attr]
            except AttributeError:
                return config[key] if key in config else default  # type: ignore[index]

        templates_root = Path(get("templates_root") or (plugin_dir / "templates"))
        return cls(
            paseo_url=str(get("paseo_url", "http://paseo:6767")),
            paseo_password=str(get("paseo_password", "")),
            internal_token=str(get("internal_token", "")),
            gateway_url=str(get("gateway_url", "http://gateway:8080")),
            public_base_url=str(get("public_base_url", "")),
            reports_root=Path(get("reports_root") or "/srv/alicedev/reports"),
            templates_root=templates_root,
            images_root=Path(
                get("images_root")
                or (plugin_dir / "data" / "plugin_data" / "alicedev" / "images")
            ),
            stickers_root=Path(get("stickers_root") or (templates_root / "stickers")),
            allowed_chats=_as_tuple(get("allowed_chats")),
            admin_users=_as_tuple(get("admin_users")),
            github_token=(str(get("github_token")) or None) if get("github_token") else None,
            default_repo=str(get("default_repo", "TraderAlice/OpenAlice")),
            paseo_provider=str(get("paseo_provider", "omp-alicedev")),
            paseo_model=str(get("paseo_model", "")),
            paseo_thinking=str(get("paseo_thinking", "high")),
            paseo_cwd=str(get("paseo_cwd", "/workspace/alicedev")),
            internal_api_host=str(get("internal_api_host", "0.0.0.0")),
            internal_api_port=int(get("internal_api_port", 6200)),
            idle_close_seconds=int(get("idle_close_seconds", 12 * 3600)),
            sweeper_interval_seconds=int(get("sweeper_interval_seconds", 600)),
            inject_wait_max_seconds=int(get("inject_wait_max_seconds", 600)),
            inject_poll_seconds=float(get("inject_poll_seconds", 2.0)),
        )
