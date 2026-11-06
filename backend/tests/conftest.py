"""Test fixtures. Tests run against dedicated *_Test databases on the configured
SQL Server and a temporary data folder; the development databases are never
touched."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="salesflow_test_"))
os.environ.update({
    "SALESFLOW_ENV": "test",
    "SALESFLOW_OPS_DB": "SalesFlow_Ops_Test",
    "SALESFLOW_DW_DB": "SalesFlow_DW_Test",
    "SALESFLOW_DATA_DIR": str(_TMP / "data"),
    "SALESFLOW_LOG_FORMAT": "text",
    "SALESFLOW_LOG_LEVEL": "WARNING",
    "SALESFLOW_STALE_RUN_AFTER_SECONDS": "10",
    "SALESFLOW_HEARTBEAT_SECONDS": "1",
    "SALESFLOW_WORKER_POLL_SECONDS": "0.5",
    "SALESFLOW_READ_ONLY": "false",
})
os.environ.pop("SALESFLOW_ADMIN_TOKEN", None)

import pytest  # noqa: E402

from salesflow.config import get_settings  # noqa: E402

get_settings.cache_clear()

from salesflow.devtools import reset_project_data  # noqa: E402
from salesflow.generator.baseline import GenConfig  # noqa: E402
from salesflow.generator.io import load_manifest, reset_landing, seed_ops, write_dataset  # noqa: E402
from salesflow.migrate import migrate  # noqa: E402
from salesflow.pipeline.executor import run_now  # noqa: E402

TEST_CONFIG = GenConfig(seed=7, customers=300, products=60, orders=1500)


@pytest.fixture(scope="session")
def settings():
    s = get_settings()
    assert s.dw_db.endswith("_Test") and s.ops_db.endswith("_Test"), "tests must use test databases"
    migrate(s)
    write_dataset(TEST_CONFIG, s)
    return s


@pytest.fixture(scope="session")
def manifest(settings):
    return load_manifest(settings)


def reset_all(settings) -> None:
    reset_project_data(settings.dw_db, settings)


@pytest.fixture
def fresh(settings, manifest):
    """Baseline seeded and loaded with an initial run."""
    reset_all(settings)
    seed_ops(settings)
    reset_landing(settings)
    run_id, status = run_now("initial", "test", settings=settings)
    assert status == "succeeded"
    return manifest
