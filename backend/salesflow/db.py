"""SQL Server engines for the operational (source) and warehouse databases."""

from __future__ import annotations

import json
import re
import threading
from typing import Any, Iterable, Literal

from sqlalchemy import URL, Engine, create_engine, text
from sqlalchemy.engine import Connection

from .config import Settings, get_settings

DbKind = Literal["ops", "dw", "master"]

_engines: dict[tuple[str, str, str], Engine] = {}
_lock = threading.Lock()
_SAFE_DB_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,99}$")


def validate_db_name(name: str) -> str:
    if not _SAFE_DB_NAME.match(name):
        raise ValueError(f"Unsafe database name: {name!r}")
    return name


def build_url(settings: Settings, database: str) -> URL:
    query: dict[str, str] = {
        "driver": settings.odbc_driver,
        "Encrypt": "yes" if settings.sql_encrypt else "no",
        "TrustServerCertificate": "yes" if settings.sql_trust_server_certificate else "no",
    }
    if settings.sql_auth == "windows":
        query["Trusted_Connection"] = "yes"
        return URL.create("mssql+pyodbc", host=settings.sql_server, database=database, query=query)
    return URL.create(
        "mssql+pyodbc",
        username=settings.sql_user,
        password=settings.sql_password.get_secret_value() if settings.sql_password else None,
        host=settings.sql_server,
        database=database,
        query=query,
    )


def get_engine(kind: DbKind, settings: Settings | None = None) -> Engine:
    settings = settings or get_settings()
    database = {"ops": settings.ops_db, "dw": settings.dw_db, "master": "master"}[kind]
    validate_db_name(database)
    key = (settings.sql_server, database, settings.sql_auth)
    with _lock:
        eng = _engines.get(key)
        if eng is None:
            eng = create_engine(
                build_url(settings, database),
                pool_pre_ping=True,
                pool_size=8,
                max_overflow=8,
                isolation_level="AUTOCOMMIT" if kind == "master" else "READ COMMITTED",
            )
            _engines[key] = eng
        return eng


def dispose_engines() -> None:
    with _lock:
        for eng in _engines.values():
            eng.dispose()
        _engines.clear()


def bulk_json_param(rows: Iterable[dict[str, Any]]) -> str:
    """Serialise rows to a JSON array for set-based OPENJSON inserts/merges.

    Decimals and datetimes are serialised as strings so SQL Server converts them
    exactly (no binary floating point anywhere in the money path).
    """
    return json.dumps(list(rows), default=str, ensure_ascii=False)


def chunked(seq: list[Any], size: int) -> Iterable[list[Any]]:
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def scalar(conn: Connection, sql: str, **params: Any) -> Any:
    return conn.execute(text(sql), params).scalar()
