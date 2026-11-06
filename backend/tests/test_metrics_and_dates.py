"""Deterministic metric examples on a hand-built micro dataset (12, 13) plus
concurrency / recovery behaviour of the durable run queue."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import text

from salesflow.analytics import queries
from salesflow.db import get_engine
from salesflow.generator.io import _upsert_ops, _ser_row
from salesflow.generator.scenarios import payments_json, products_csv
from salesflow.pipeline.executor import execute_run, run_now
from salesflow.pipeline.runs import ActiveRunExists, claim, create_retry, create_run, pipeline_lock, recover_stale_runs

from conftest import reset_all
from helpers import scalar

T0 = datetime(2026, 7, 1)


def _write_micro(settings) -> None:
    reset_all(settings)
    customers = [{"customer_id": i, "customer_code": f"C{i:05d}", "first_name": "Test", "last_name": f"Person{i}",
                  "email": f"t{i}@example.com", "city": "Riyadh", "created_at": T0, "updated_at": T0} for i in (1, 2)]
    placed_1001 = datetime(2026, 8, 1, 7, 15)          # 10:15 Riyadh, 1 Aug
    placed_1002 = datetime(2026, 8, 31, 22, 30)        # 01:30 Riyadh, 1 Sep (UTC date is 31 Aug)
    placed_1003 = datetime(2026, 8, 2, 9, 0)           # cancelled order, 2 Aug
    orders = [
        {"order_id": 1001, "customer_id": 1, "order_placed_at": placed_1001, "status": "delivered", "currency": "SAR",
         "channel": "web", "created_at": placed_1001, "updated_at": placed_1001 + timedelta(days=3)},
        {"order_id": 1002, "customer_id": 2, "order_placed_at": placed_1002, "status": "delivered", "currency": "SAR",
         "channel": "app", "created_at": placed_1002, "updated_at": placed_1002 + timedelta(days=2)},
        {"order_id": 1003, "customer_id": 2, "order_placed_at": placed_1003, "status": "cancelled", "currency": "SAR",
         "channel": "app", "created_at": placed_1003, "updated_at": placed_1003 + timedelta(hours=1)},
    ]
    items = [
        {"order_item_id": 1, "order_id": 1001, "line_number": 1, "product_sku": "SKU-00001", "quantity": 2,
         "unit_price": "100.00", "discount_amount": "0.00", "created_at": placed_1001, "updated_at": placed_1001},
        {"order_item_id": 2, "order_id": 1001, "line_number": 2, "product_sku": "SKU-00002", "quantity": 1,
         "unit_price": "300.00", "discount_amount": "0.00", "created_at": placed_1001, "updated_at": placed_1001},
        {"order_item_id": 3, "order_id": 1002, "line_number": 1, "product_sku": "SKU-00002", "quantity": 1,
         "unit_price": "300.00", "discount_amount": "30.00", "created_at": placed_1002, "updated_at": placed_1002},
        {"order_item_id": 4, "order_id": 1003, "line_number": 1, "product_sku": "SKU-00001", "quantity": 2,
         "unit_price": "100.00", "discount_amount": "0.00", "created_at": placed_1003, "updated_at": placed_1003},
    ]
    with get_engine("ops", settings).begin() as conn:
        for t, rows in (("customers", customers), ("orders", orders), ("order_items", items)):
            conn.execute(text(f"DELETE FROM dbo.{t}"))
        for t, rows in (("customers", customers), ("orders", orders), ("order_items", items)):
            _upsert_ops(conn, t, [_ser_row(t, r) for r in rows])
    land = settings.landing_dir
    (land / "products").mkdir(parents=True, exist_ok=True)
    (land / "payments").mkdir(parents=True, exist_ok=True)
    from decimal import Decimal
    prods = [{"product_sku": "SKU-00001", "product_name": "Kettle", "category": "Home & Kitchen", "list_price": Decimal("100.00"),
              "currency": "SAR", "is_active": True, "updated_at": T0},
             {"product_sku": "SKU-00002", "product_name": "Speaker", "category": "Electronics", "list_price": Decimal("300.00"),
              "currency": "SAR", "is_active": True, "updated_at": T0}]
    (land / "products" / "products_v001.csv").write_text(products_csv(prods), encoding="utf-8")

    def tx(tid, oid, ttype, status, amount, at, ref=None):
        return {"transaction_id": tid, "order_id": oid, "transaction_type": ttype, "status": status, "amount": amount,
                "currency": "SAR", "payment_method": "mada", "transaction_at": at, "refers_to_transaction_id": ref}
    (land / "payments" / "payments_micro_1.json").write_text(payments_json("m1", [
        tx("PAY-F-1", 1001, "payment", "failed", "500.00", "2026-08-01T07:15:30Z"),
        tx("PAY-1", 1001, "payment", "succeeded", "500.00", "2026-08-01T07:16:00Z"),
        tx("PAY-2", 1002, "payment", "succeeded", "270.00", "2026-08-31T22:31:00Z"),   # 1 Sep Riyadh
    ], T0), encoding="utf-8")
    (land / "payments" / "payments_micro_2.json").write_text(payments_json("m2", [
        tx("RFD-1", 1001, "refund", "succeeded", "100.00", "2026-08-09T09:00:00Z", "PAY-1"),
    ], T0), encoding="utf-8")


@pytest.fixture
def micro(settings):
    _write_micro(settings)
    run_id, status = run_now("initial", "test", settings=settings)
    assert status == "succeeded", queries.get_run(run_id)["error"]
    return settings


def _m(d1: date, d2: date) -> dict:
    return {k: v["value"] for k, v in queries.overview(d1, d2)["metrics"].items()}


# 12 -----------------------------------------------------------------------------------------
def test_order_1001_lifetime_values(micro):
    t = queries.order_detail(1001)["totals"]
    assert t["gross_merchandise_value"] == "500.00"
    assert t["ordered_value"] == "500.00"
    assert t["successful_collections"] == "500.00"     # the failed attempt is excluded
    assert t["successful_refunds"] == "100.00"
    assert t["net_collections"] == "400.00"
    assert t["collection_state"] == "partially_refunded"


def test_payment_and_refund_on_different_dates(micro):
    aug1 = _m(date(2026, 8, 1), date(2026, 8, 1))
    assert (aug1["gross_merchandise_value"], aug1["successful_collections"], aug1["successful_refunds"],
            aug1["net_collections"]) == ("500.00", "500.00", "0.00", "500.00")
    assert aug1["failed_payment_attempts"] == 1
    aug9 = _m(date(2026, 8, 9), date(2026, 8, 9))
    assert (aug9["gross_merchandise_value"], aug9["successful_collections"], aug9["successful_refunds"],
            aug9["net_collections"]) == ("0.00", "0.00", "100.00", "-100.00")
    assert aug9["average_order_value"] is None and aug9["distinct_active_orders"] == 0   # zero denominator -> n/a
    month = _m(date(2026, 8, 1), date(2026, 8, 31))
    assert (month["successful_collections"], month["successful_refunds"], month["net_collections"]) == ("500.00", "100.00", "400.00")
    pts = {p["bucket"]: p for p in queries.timeseries(date(2026, 8, 1), date(2026, 8, 10), "day")["points"]}
    assert pts["2026-08-01"]["successful_collections"] == "500.00" and pts["2026-08-09"]["successful_refunds"] == "100.00"
    assert pts["2026-08-09"]["active_ordered_value"] == "0.00"


# 11 (micro) ---------------------------------------------------------------------------------
def test_cancelled_order_in_ordered_value_but_not_active(micro):
    aug2 = _m(date(2026, 8, 2), date(2026, 8, 2))
    assert aug2["gross_merchandise_value"] == "200.00" and aug2["ordered_value"] == "200.00"
    assert aug2["active_ordered_value"] == "0.00" and aug2["distinct_active_orders"] == 0
    aug = _m(date(2026, 8, 1), date(2026, 8, 31))
    assert aug["ordered_value"] == "700.00" and aug["active_ordered_value"] == "500.00"
    assert aug["distinct_active_orders"] == 1 and aug["average_order_value"] == "500.00"


# 13 -----------------------------------------------------------------------------------------
def test_date_filters_use_riyadh_business_dates(micro):
    # Order 1002 was placed 2026-08-31 22:30 UTC = 2026-09-01 01:30 Asia/Riyadh.
    aug31 = _m(date(2026, 8, 31), date(2026, 8, 31))
    assert aug31["ordered_value"] == "0.00" and aug31["successful_collections"] == "0.00"
    sep1 = _m(date(2026, 9, 1), date(2026, 9, 1))
    assert sep1["ordered_value"] == "270.00" and sep1["gross_merchandise_value"] == "300.00"
    assert sep1["successful_collections"] == "270.00"
    listed = queries.list_orders({"date_from": date(2026, 8, 31), "date_to": date(2026, 8, 31)}, "order_id", "asc", 1, 10)
    assert listed["total"] == 0
    listed = queries.list_orders({"date_from": date(2026, 9, 1), "date_to": date(2026, 9, 1)}, "order_id", "asc", 1, 10)
    assert [i["order_id"] for i in listed["items"]] == [1002]
    assert listed["items"][0]["order_date"] == "2026-09-01"
    # Sales use the order date, collections the transaction date: refund of 1001 is in a period without its order.
    ov = queries.overview(date(2026, 8, 5), date(2026, 8, 12))
    assert ov["metrics"]["ordered_value"]["value"] == "0.00" and ov["metrics"]["successful_refunds"]["value"] == "100.00"


def test_previous_period_comparison_is_equal_length_and_explicit(micro):
    ov = queries.overview(date(2026, 9, 1), date(2026, 9, 30))
    assert ov["comparison"]["date_from"] == "2026-08-02" and ov["comparison"]["date_to"] == "2026-08-31"
    assert ov["comparison"]["days"] == ov["period"]["days"] == 30
    assert ov["comparison"]["available"] is True
    early = queries.overview(date(2026, 8, 1), date(2026, 8, 31))
    assert early["comparison"]["available"] is False and "before the first available data" in early["comparison"]["reason"]
    assert early["metrics"]["ordered_value"]["previous"] is None and early["metrics"]["ordered_value"]["change_pct"] is None
    # Previous value of zero -> percentage change is n/a rather than infinite.
    ov2 = queries.overview(date(2026, 8, 9), date(2026, 8, 9))
    assert ov2["comparison"]["available"] is True
    assert ov2["metrics"]["successful_refunds"]["previous"] == "0.00"
    assert ov2["metrics"]["successful_refunds"]["change_pct"] is None


# Queue, locking and recovery ----------------------------------------------------------------
def test_only_one_run_can_be_queued_or_running(micro, settings):
    rid = create_run("incremental", "test", settings=settings)
    with pytest.raises(ActiveRunExists) as e:
        create_run("initial", "test", settings=settings)
    assert e.value.run_id == rid
    claim("test-worker", rid, settings)
    assert execute_run(rid, "test-worker", settings) == "succeeded"
    create_run("incremental", "test", settings=settings)  # slot is free again
    with get_engine("dw", settings).begin() as c:
        c.execute(text("UPDATE meta.pipeline_run SET status = 'failed', active_slot = NULL WHERE status = 'queued'"))


def test_execution_refuses_to_run_while_lock_is_held(micro, settings):
    with pipeline_lock(settings) as lock:
        assert lock is not None
        run_id, status = run_now("incremental", "test", settings=settings)
    assert status == "failed"
    assert queries.get_run(run_id)["error"]["code"] == "CONCURRENT_RUN"


def test_interrupted_worker_is_recovered_and_retry_succeeds(micro, settings):
    rid = create_run("incremental", "test", settings=settings)
    claim("dead-worker", rid, settings)
    with get_engine("dw", settings).begin() as c:  # worker vanished: heartbeat goes stale
        c.execute(text("UPDATE meta.pipeline_run SET heartbeat_at_utc = DATEADD(MINUTE, -10, SYSUTCDATETIME()), "
                       "current_stage = 'stage_validate' WHERE run_id = :r"), {"r": rid})
    assert recover_stale_runs(settings) == [rid]
    run = queries.get_run(rid)
    assert run["status"] == "failed" and run["error"]["code"] == "WORKER_INTERRUPTED"
    retry = create_retry(rid, "test", settings)
    claim("w2", retry, settings)
    assert execute_run(retry, "w2", settings) == "succeeded"
    assert queries.order_detail(1001)["totals"]["net_collections"] == "400.00"


def test_fault_injection_disabled_in_production(settings):
    from salesflow.config import Settings
    from salesflow.pipeline.common import PipelineError
    from salesflow.pipeline.runs import validate_request
    prod = Settings(env="production", admin_token="x")
    with pytest.raises(PipelineError):
        validate_request("incremental", {"fail_at": "extract"}, prod)
    with pytest.raises(PipelineError):
        validate_request("reprocess", {"scope": "nope"}, settings)
    assert json.dumps(validate_request("reprocess", {"scope": "quarantine", "error_codes": ["X"]}, settings))
