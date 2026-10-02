from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    app_env: str = "development"
    apps_dir: Path = Path("app")
    apps_auto_reload: bool = True
    apps_reload_interval_seconds: float = Field(default=1.0, ge=0.1, le=60.0)
    apps_reload_debounce_seconds: float = Field(default=0.5, ge=0.1, le=60.0)
    bootstrap_admin_key: str = ""
    jwt_secret: str = ""
    operator_token: str = ""
    readiness_timeout_seconds: float = Field(default=3.0, gt=0, le=60)
    readiness_max_concurrency: int = Field(default=8, ge=1, le=64)

    # The remote editor control plane is deliberately disabled by default. It
    # has an independent credential and network policy from application API
    # keys so an editor token cannot be used as an application credential.
    editor_api_enabled: bool = False
    # EDITOR_TOKEN is the one-time founder setup secret. Disable setup and
    # remove it from the environment after the founder account is created.
    editor_token: str = ""
    editor_setup_enabled: bool = True
    editor_legacy_token_enabled: bool = False
    editor_require_https: bool = True
    editor_allowed_ips: str = "127.0.0.1/32,::1/128"
    editor_trusted_proxy_cidrs: str = ""
    editor_trusted_hosts: str = "localhost,127.0.0.1,[::1]"
    editor_read_only: bool = False
    editor_allow_create_projects: bool = False
    editor_allow_hooks: bool = False
    editor_allow_graphs: bool = False
    editor_allowed_projects: str = ""
    editor_max_document_bytes: int = Field(default=2 * 1024 * 1024, ge=1024, le=16 * 1024 * 1024)
    editor_session_ttl_seconds: int = Field(default=12 * 60 * 60, ge=300, le=7 * 24 * 60 * 60)
    editor_session_idle_seconds: int = Field(default=60 * 60, ge=60, le=24 * 60 * 60)
    editor_session_bind_user_agent: bool = True
    editor_password_min_length: int = Field(default=12, ge=12, le=64)
    editor_login_max_attempts: int = Field(default=5, ge=3, le=20)
    editor_login_window_seconds: int = Field(default=5 * 60, ge=60, le=60 * 60)
    editor_login_lock_seconds: int = Field(default=15 * 60, ge=60, le=24 * 60 * 60)
    editor_collaboration_enabled: bool = True
    editor_calls_enabled: bool = True
    editor_call_ticket_ttl_seconds: int = Field(default=60, ge=15, le=300)
    editor_call_signal_ttl_seconds: int = Field(default=5 * 60, ge=60, le=60 * 60)
    editor_call_ice_servers_json: str = '[{"urls":"stun:stun.cloudflare.com:3478"}]'
    editor_database_browser_enabled: bool = True
    editor_database_expose_undeclared: bool = False
    editor_database_row_limit: int = Field(default=100, ge=1, le=500)
    editor_max_message_chars: int = Field(default=8_000, ge=256, le=64_000)
    editor_max_note_chars: int = Field(default=128_000, ge=1_024, le=1_000_000)
    editor_attachment_dir: Path = Path("data/editor-attachments")
    editor_max_attachment_bytes: int = Field(default=25 * 1024 * 1024, ge=1024, le=250 * 1024 * 1024)

    internal_database_url: str = "sqlite+aiosqlite:///./data/internal-v4.db"
    internal_pool_pre_ping: bool = True
    internal_pool_size: int = Field(default=10, ge=1)
    internal_max_overflow: int = Field(default=20, ge=0)
    internal_pool_timeout: int = Field(default=30, ge=1)
    internal_pool_recycle: int = Field(default=1800, ge=-1)
    internal_schema_mode: str = "create"

    primary_database_url: str = "sqlite+aiosqlite:///./data/app.db"
    mysql_database_url: str | None = None
    redis_url: str | None = None

    log_level: str = "INFO"

    model_config = SettingsConfigDict(
        env_file=Path.cwd() / ".env",
        extra="ignore",
        case_sensitive=False,
    )


def load_settings(*, root: Path | str | None = None, **overrides: Any) -> Settings:
    """Load process settings relative to a deployment root."""
    env_file = Path(root).resolve() / ".env" if root is not None else Path.cwd() / ".env"

    class RootSettings(Settings):
        model_config = SettingsConfigDict(env_file=env_file, extra="ignore", case_sensitive=False)

    return RootSettings(**overrides)


settings = load_settings()
