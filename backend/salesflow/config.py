"""Environment-based configuration.

All settings come from environment variables prefixed with ``SALESFLOW_`` or from a
``.env`` file at the repository root. Nothing secret is stored in source control.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]
BUSINESS_TIMEZONE = "Asia/Riyadh"
CURRENCY = "SAR"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SALESFLOW_",
        env_file=(REPO_ROOT / ".env",),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: Literal["development", "test", "production"] = "development"

    # SQL Server connection
    sql_server: str = r"localhost\SQLEXPRESS"
    sql_auth: Literal["windows", "sql"] = "windows"
    sql_user: str | None = None
    sql_password: SecretStr | None = None
    odbc_driver: str = "ODBC Driver 18 for SQL Server"
    sql_encrypt: bool = True
    sql_trust_server_certificate: bool = True
    ops_db: str = "SalesFlow_Ops"
    dw_db: str = "SalesFlow_DW"

    # Files
    data_dir: Path = REPO_ROOT / "data"

    # Pipeline behaviour
    extract_overlap_seconds: int = Field(default=300, ge=0, le=86400)
    worker_poll_seconds: float = Field(default=2.0, gt=0)
    heartbeat_seconds: float = Field(default=5.0, gt=0)
    stale_run_after_seconds: int = Field(default=60, ge=10)

    # API
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    cors_origins: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]
    web_url: str = "http://localhost:3000"  # where GET / on the API redirects
    read_only: bool = False
    admin_token: SecretStr | None = None
    embedded_worker: bool = False
    max_page_size: int = 100
    max_export_rows: int = 50_000

    # Extra SQL Server hosts (besides localhost) where the dev reset may run, e.g. ["sqlserver"] in Docker.
    reset_allowed_servers: list[str] = []

    log_level: str = "INFO"
    log_format: Literal["json", "text"] = "json"

    @field_validator("data_dir", mode="before")
    @classmethod
    def _resolve_data_dir(cls, v: object) -> Path:
        p = Path(str(v))
        return p if p.is_absolute() else (REPO_ROOT / p).resolve()

    @property
    def landing_dir(self) -> Path:
        return self.data_dir / "landing"

    @property
    def baseline_dir(self) -> Path:
        return self.data_dir / "baseline"

    @property
    def scenarios_dir(self) -> Path:
        return self.data_dir / "scenarios"

    @property
    def fault_injection_allowed(self) -> bool:
        return self.env in ("development", "test")

    @property
    def mutations_allowed(self) -> bool:
        """Mutating endpoints are never open in production without a token."""
        if self.read_only:
            return False
        if self.env == "production" and self.admin_token is None:
            return False
        return True


@lru_cache
def get_settings() -> Settings:
    return Settings()
