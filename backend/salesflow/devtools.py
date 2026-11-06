"""Development-only reset, scoped to this project's databases and data folder."""

from __future__ import annotations

import re

from sqlalchemy import text

from .config import Settings, get_settings
from .db import get_engine
from .generator.io import _clean_dir

LOCAL_SERVERS = re.compile(r"^(localhost|127\.0\.0\.1|\.|\(local\)|\[::1\])([\\,].*)?$", re.IGNORECASE)
DW_TABLES = [  # child tables first
    "qa.reconciliation_result", "qa.check_result", "qa.quarantine_record",
    "dw.FactPaymentTransaction", "dw.FactSalesLine", "dw.DimOrder", "dw.DimProduct", "dw.DimCustomer",
    "stg.payment_txn", "stg.order_item", "stg.sales_order", "stg.product", "stg.customer",
    "raw.source_record", "meta.source_file", "meta.watermark", "meta.run_entity_stats",
    "meta.pipeline_run_stage", "meta.pipeline_run",
]
OPS_TABLES = ["dbo.order_items", "dbo.orders", "dbo.customers"]


class ResetRefused(Exception):
    pass


def assert_reset_allowed(settings: Settings, confirm: str | None) -> None:
    if settings.env not in ("development", "test"):
        raise ResetRefused(f"Reset is only allowed when SALESFLOW_ENV is development or test (it is {settings.env}).")
    for db in (settings.ops_db, settings.dw_db):
        if not db.startswith("SalesFlow_"):
            raise ResetRefused(f"Refusing to reset database {db!r}: project databases must be named SalesFlow_*.")
    host = re.split(r"[\\,]", settings.sql_server)[0].lower()
    if not LOCAL_SERVERS.match(settings.sql_server) and host not in {h.lower() for h in settings.reset_allowed_servers}:
        raise ResetRefused(f"Refusing to reset a non-local SQL Server ({settings.sql_server}).")
    if confirm != settings.dw_db:
        raise ResetRefused(f"Confirmation required: pass --confirm {settings.dw_db}")


def reset_project_data(confirm: str | None, settings: Settings | None = None, include_ops: bool = True) -> dict:
    """Delete all rows from SalesFlow tables and empty the landing zone.

    Keeps schemas, migrations and dw.DimDate. Never touches other databases.
    """
    settings = settings or get_settings()
    assert_reset_allowed(settings, confirm)
    with get_engine("dw", settings).begin() as conn:
        active = conn.execute(text("SELECT COUNT(*) FROM meta.pipeline_run WHERE status = 'running'")).scalar()
        if active:
            raise ResetRefused("A pipeline run is in progress; wait for it to finish.")
        for t in DW_TABLES:
            conn.execute(text(f"DELETE FROM {t}"))
        for t in ("meta.pipeline_run", "meta.source_file", "raw.source_record", "qa.quarantine_record",
                  "qa.check_result", "qa.reconciliation_result", "dw.DimCustomer", "dw.DimProduct", "dw.DimOrder",
                  "dw.FactSalesLine", "dw.FactPaymentTransaction"):
            conn.execute(text(f"DBCC CHECKIDENT ('{t}', RESEED, 0) WITH NO_INFOMSGS"))
    if include_ops:
        with get_engine("ops", settings).begin() as conn:
            for t in OPS_TABLES:
                conn.execute(text(f"DELETE FROM {t}"))
    _clean_dir(settings.landing_dir, settings.data_dir)
    return {"warehouse_tables_cleared": len(DW_TABLES), "ops_cleared": include_ops, "landing": str(settings.landing_dir)}
