"""Minimal versioned SQL migration runner.

Each database has a folder of ``VNNN__description.sql`` files. Files are split on
lines containing only ``GO`` and applied in one transaction per file. Applied
versions are recorded in ``dbo.schema_migrations``.
"""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path

from sqlalchemy import text

from .config import Settings, get_settings
from .db import get_engine, validate_db_name

log = logging.getLogger(__name__)
MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_GO = re.compile(r"^\s*GO\s*$", re.IGNORECASE | re.MULTILINE)


def _ensure_database(name: str, settings: Settings, rcsi: bool) -> None:
    validate_db_name(name)
    master = get_engine("master", settings)
    with master.connect() as conn:
        exists = conn.execute(text("SELECT DB_ID(:n)"), {"n": name}).scalar()
        if exists is None:
            log.info("creating database", extra={"database": name})
            conn.execute(text(f"CREATE DATABASE [{name}]"))
        if rcsi:
            # Readers see the last committed warehouse state while a pipeline
            # transaction is publishing (no blocking, no dirty reads).
            on = conn.execute(
                text("SELECT is_read_committed_snapshot_on FROM sys.databases WHERE name = :n"), {"n": name}
            ).scalar()
            if not on:
                conn.execute(text(f"ALTER DATABASE [{name}] SET READ_COMMITTED_SNAPSHOT ON WITH ROLLBACK IMMEDIATE"))


def _apply(kind: str, settings: Settings) -> list[str]:
    engine = get_engine(kind, settings)  # type: ignore[arg-type]
    applied: list[str] = []
    with engine.begin() as conn:
        conn.execute(text(
            """IF OBJECT_ID('dbo.schema_migrations') IS NULL
               CREATE TABLE dbo.schema_migrations (
                   version NVARCHAR(20) NOT NULL PRIMARY KEY,
                   name NVARCHAR(200) NOT NULL,
                   checksum CHAR(64) NOT NULL,
                   applied_at_utc DATETIME2(3) NOT NULL DEFAULT SYSUTCDATETIME())"""
        ))
        done = {r[0]: r[1] for r in conn.execute(text("SELECT version, checksum FROM dbo.schema_migrations"))}
    for path in sorted((MIGRATIONS_DIR / kind).glob("V*__*.sql")):
        version, name = path.stem.split("__", 1)
        body = path.read_text(encoding="utf-8")
        checksum = hashlib.sha256(body.encode()).hexdigest()
        if version in done:
            if done[version] != checksum:
                raise RuntimeError(f"Migration {path.name} changed after it was applied")
            continue
        with engine.begin() as conn:
            for batch in _GO.split(body):
                if batch.strip():
                    conn.exec_driver_sql(batch)
            conn.execute(
                text("INSERT INTO dbo.schema_migrations (version, name, checksum) VALUES (:v, :n, :c)"),
                {"v": version, "n": name, "c": checksum},
            )
        log.info("applied migration", extra={"db": kind, "migration": path.name})
        applied.append(f"{kind}:{path.name}")
    return applied


def migrate(settings: Settings | None = None) -> list[str]:
    settings = settings or get_settings()
    _ensure_database(settings.ops_db, settings, rcsi=False)
    _ensure_database(settings.dw_db, settings, rcsi=True)
    return _apply("ops", settings) + _apply("dw", settings)
