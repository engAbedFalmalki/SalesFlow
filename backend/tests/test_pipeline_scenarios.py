"""End-to-end pipeline behaviour against SQL Server, driven by the deterministic
scenarios in the generated manifest. Each test starts from a freshly loaded
baseline (fixture ``fresh``)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from salesflow.analytics import queries
from salesflow.generator.io import apply_scenario
from salesflow.pipeline.executor import run_now
from salesflow.scenario_runner import run_scenario

from helpers import (expected_totals, open_quarantine_by_code, rows, scalar, scenario, unresolved_reconciliation,
                     warehouse_totals)


# 1 ------------------------------------------------------------------------------------------
def test_initial_load_expected_counts_and_totals(fresh):
    assert warehouse_totals() == expected_totals(fresh)
    assert open_quarantine_by_code() == {}
    states = {r["state"] for r in rows("SELECT state FROM qa.reconciliation_result WHERE run_id = 1")}
    assert states <= {"matched", "informational"}
    stats = {r["entity"]: r for r in rows("SELECT * FROM meta.run_entity_stats WHERE run_id = 1")}
    base = fresh["baseline"]["ops_tables"]
    assert stats["order"]["rows_read"] == base["orders"] == stats["order"]["rows_inserted"]
    assert stats["order_item"]["rows_inserted"] == base["order_items"]


# 2 ------------------------------------------------------------------------------------------
def test_repeating_the_same_batch_creates_no_duplicates(fresh, settings):
    before = warehouse_totals()
    raw_before = scalar("SELECT COUNT(*) FROM raw.source_record")
    for mode in ("incremental", "initial"):  # initial re-reads the whole source
        run_id, status = run_now(mode, "test", settings=settings)
        assert status == "succeeded"
        st = rows("SELECT * FROM meta.run_entity_stats WHERE run_id = :r", r=run_id)
        assert all(s["rows_inserted"] == 0 and s["rows_updated"] == 0 and s["rows_new_version"] == 0 for s in st)
    apply_scenario("s01_initial_load", settings)  # re-deliver every baseline file and re-seed the source
    run_id, status = run_now("incremental", "test", settings=settings)
    assert status == "succeeded"
    assert warehouse_totals() == before
    assert scalar("SELECT COUNT(*) FROM raw.source_record") == raw_before


# 3 ------------------------------------------------------------------------------------------
def test_existing_order_update_reaches_warehouse(fresh, settings):
    exp = scenario(fresh, "s03_order_updates")["expected"]
    [res] = run_scenario("s03_order_updates", settings)
    assert res["status"] == "succeeded"
    assert warehouse_totals() == expected_totals(fresh, "s03_order_updates")
    stats = {r["entity"]: r for r in rows("SELECT * FROM meta.run_entity_stats WHERE run_id = :r", r=res["run_id"])}
    assert stats["order"]["rows_updated"] == exp["dim_order_updated"]
    assert stats["order_item"]["rows_updated"] == exp["sales_lines_updated"]
    for oid in exp["cancelled_order_ids"]:
        assert queries.order_detail(oid)["order"]["status"] == "cancelled"
    for oid in exp["delivered_order_ids"]:
        assert queries.order_detail(oid)["order"]["status"] == "delivered"


# 4 ------------------------------------------------------------------------------------------
def test_late_arriving_payment_is_included_on_its_transaction_date(fresh, settings):
    exp = scenario(fresh, "s04_late_payments")["expected"]
    oct_from, oct_to = date(2026, 10, 1), date(2026, 10, 31)
    before = Decimal(queries.overview(oct_from, oct_to)["metrics"]["successful_collections"]["value"])
    assert exp["late_collections_month"] == "2026-10"
    [res] = run_scenario("s04_late_payments", settings)
    assert res["status"] == "succeeded"
    after = Decimal(queries.overview(oct_from, oct_to)["metrics"]["successful_collections"]["value"])
    assert after - before == Decimal(exp["late_collections_amount"])
    found = scalar("SELECT COUNT(*) FROM dw.FactPaymentTransaction WHERE TransactionId IN (SELECT value FROM OPENJSON(:j))",
                   j=__import__("json").dumps(exp["late_transaction_ids"]))
    assert found == len(exp["late_transaction_ids"])
    assert warehouse_totals() == expected_totals(fresh, "s04_late_payments")


# 5 ------------------------------------------------------------------------------------------
def test_duplicate_file_delivery_is_detected_and_skipped(fresh, settings):
    before = warehouse_totals()
    raw_before = scalar("SELECT COUNT(*) FROM raw.source_record")
    [res] = run_scenario("s05_duplicate_file", settings)
    assert res["status"] == "succeeded"
    files = rows("SELECT file_name, status, duplicate_of_file_id, message FROM meta.source_file WHERE first_seen_run_id = :r",
                 r=res["run_id"])
    by_status = {}
    for f in files:
        by_status[f["status"]] = by_status.get(f["status"], 0) + 1
        assert f["message"]
    assert by_status == {"duplicate": 2, "conflict": 1}
    assert all(f["duplicate_of_file_id"] for f in files)
    assert warehouse_totals() == before
    assert scalar("SELECT COUNT(*) FROM raw.source_record") == raw_before
    # Running again does not register the same deliveries twice.
    run_id, _ = run_now("incremental", "test", settings=settings)
    assert scalar("SELECT COUNT(*) FROM meta.source_file WHERE first_seen_run_id = :r", r=run_id) == 0


# 6 ------------------------------------------------------------------------------------------
def test_invalid_records_are_quarantined_with_reasons(fresh, settings):
    exp = scenario(fresh, "s08_invalid_values")["expected"]
    [res] = run_scenario("s08_invalid_values", settings)
    assert res["status"] == "succeeded"
    assert open_quarantine_by_code() == exp["quarantine_open"]
    q = rows("SELECT * FROM qa.quarantine_record WHERE status = 'open'")
    for r in q:
        assert r["error_reason"] and r["payload"] and r["run_id"] == res["run_id"] and r["source_record_id"]
        assert r["errors_json"].startswith("[")
    assert warehouse_totals() == expected_totals(fresh, "s08_invalid_values")
    assert unresolved_reconciliation() == []
    explained = rows("SELECT metric FROM qa.reconciliation_result WHERE run_id = :r AND state = 'explained'", r=res["run_id"])
    assert explained, "quarantined rows must show up as explained differences"


def test_invalid_product_references_are_quarantined(fresh, settings):
    exp = scenario(fresh, "s07_invalid_product_refs")["expected"]
    [res] = run_scenario("s07_invalid_product_refs", settings)
    assert res["status"] == "succeeded"
    assert open_quarantine_by_code() == exp["quarantine_open"]
    ids = {int(r["source_record_id"]) for r in rows("SELECT source_record_id FROM qa.quarantine_record WHERE status = 'open'")}
    assert ids == set(exp["quarantined_item_ids"])
    assert warehouse_totals() == expected_totals(fresh, "s07_invalid_product_refs")
    assert unresolved_reconciliation() == []


def test_schema_invalid_files_are_rejected_with_explanations(fresh, settings):
    exp = scenario(fresh, "s09_schema_invalid")["expected"]
    [res] = run_scenario("s09_schema_invalid", settings)
    assert res["status"] == "succeeded"
    assert open_quarantine_by_code() == exp["quarantine_open"]
    rejected = rows("SELECT file_name, message FROM meta.source_file WHERE status = 'rejected'")
    assert len(rejected) == exp["files"]["rejected"]
    assert all(len(r["message"]) > 20 for r in rejected)
    header = rows("SELECT error_reason FROM qa.quarantine_record WHERE error_code = 'SCHEMA_INVALID_HEADER'")[0]
    assert "list_price" in header["error_reason"] and "currency" in header["error_reason"]
    assert warehouse_totals() == expected_totals(fresh, "s09_schema_invalid")
    # Regression: file-level quarantine payloads are raw text, not JSON. Queries that read
    # order ids from quarantine payloads must not fail on them.
    assert queries.order_detail(1001)["totals"]["net_collections"] == "400.00"
    run_id, status = run_now("incremental", "test", settings=settings)
    assert status == "succeeded", queries.get_run(run_id)["error"]


def test_corrected_input_is_reprocessed_and_resolves_quarantine(fresh, settings):
    for sid in ("s06_catalog_price_update", "s07_invalid_product_refs", "s09_schema_invalid"):
        assert all(r["status"] == "succeeded" for r in run_scenario(sid, settings))
    assert open_quarantine_by_code("order_item") == {"MISSING_PRODUCT_REF": 6}
    results = run_scenario("s10_corrected_input", settings)
    assert [r["status"] for r in results] == ["succeeded", "succeeded"]
    assert open_quarantine_by_code("order_item") == {}
    assert scalar("SELECT COUNT(*) FROM qa.quarantine_record WHERE status = 'resolved' AND error_code = 'MISSING_PRODUCT_REF' "
                  "AND resolved_run_id = :r", r=results[1]["run_id"]) == 6
    assert scalar("SELECT status FROM meta.source_file WHERE file_name = 'products_v003.csv' AND status = 'processed'") == "processed"
    assert warehouse_totals() == expected_totals(fresh, "s06_catalog_price_update", "s07_invalid_product_refs",
                                                 "s09_schema_invalid", "s10_corrected_input")
    assert unresolved_reconciliation() == []


# 7 & 8 --------------------------------------------------------------------------------------
def test_failed_run_keeps_watermark_and_warehouse_then_retry_succeeds(fresh, settings):
    marks_before = rows("SELECT source_name, watermark_utc FROM meta.watermark ORDER BY source_name")
    totals_before = warehouse_totals()
    results = run_scenario("s11_interruption_retry", settings)
    failed, retry = results
    assert failed["status"] == "failed" and retry["status"] == "succeeded"
    run = queries.get_run(failed["run_id"])
    assert run["error"]["code"] == "INJECTED_FAILURE" and run["error"]["stage"] == "publish"
    assert {s["stage_name"]: s["status"] for s in run["stages"]}["publish"] == "failed"
    assert all(s["status"] in ("succeeded", "failed") for s in run["stages"])
    # Nothing from the failed run became visible.
    assert scalar("SELECT COUNT(*) FROM dw.DimOrder WHERE InsertedRunId = :r", r=failed["run_id"]) == 0
    assert scalar("SELECT COUNT(*) FROM meta.run_entity_stats WHERE run_id = :r", r=failed["run_id"]) == 0
    assert scalar("SELECT COUNT(*) FROM qa.reconciliation_result WHERE run_id = :r", r=failed["run_id"]) == 0
    # Raw landings of the failed run are kept for debugging...
    assert scalar("SELECT COUNT(*) FROM raw.source_record WHERE run_id = :r", r=failed["run_id"]) > 0
    # ...and the retry produced exactly the expected final state.
    assert queries.get_run(retry["run_id"])["retry_of_run_id"] == failed["run_id"]
    assert warehouse_totals() == expected_totals(fresh, "s11_interruption_retry")
    assert totals_before != warehouse_totals()
    marks_after = {r["source_name"]: r["watermark_utc"] for r in rows("SELECT source_name, watermark_utc FROM meta.watermark")}
    assert all(marks_after[m["source_name"]] > m["watermark_utc"] for m in marks_before if m["source_name"] != "ops.customers")


def test_failure_before_commit_does_not_advance_watermark(fresh, settings):
    marks = rows("SELECT source_name, watermark_utc, last_run_id FROM meta.watermark ORDER BY source_name")
    apply_scenario("s02_new_orders", settings)
    for point in ("extract", "staging", "load_warehouse", "before_commit"):
        run_id, status = run_now("incremental", "test", {"fail_at": point}, settings=settings)
        assert status == "failed"
        assert rows("SELECT source_name, watermark_utc, last_run_id FROM meta.watermark ORDER BY source_name") == marks
        assert warehouse_totals() == expected_totals(fresh)
        assert scalar("SELECT COUNT(*) FROM meta.source_file WHERE status = 'processed' AND processed_run_id = :r", r=run_id) == 0
    run_id, status = run_now("incremental", "test", settings=settings)
    assert status == "succeeded"
    assert warehouse_totals() == expected_totals(fresh, "s02_new_orders")


# 9 ------------------------------------------------------------------------------------------
def test_multiple_lines_and_payments_do_not_multiply_amounts(fresh):
    # Orders that have >= 2 lines AND >= 2 transactions are where a naive line x payment join double counts.
    multi = rows("""SELECT TOP 20 o.OrderId FROM dw.DimOrder o
                    WHERE (SELECT COUNT(*) FROM dw.FactSalesLine f WHERE f.OrderKey = o.OrderKey) >= 2
                      AND (SELECT COUNT(*) FROM dw.FactPaymentTransaction p WHERE p.OrderKey = o.OrderKey) >= 2
                    ORDER BY o.OrderId""")
    assert len(multi) >= 5
    for m in multi:
        oid = m["OrderId"]
        lines = rows("SELECT NetAmount FROM dw.FactSalesLine f JOIN dw.DimOrder o ON o.OrderKey = f.OrderKey WHERE o.OrderId = :o", o=oid)
        txns = rows("""SELECT NetCollectionAmount FROM dw.FactPaymentTransaction p JOIN dw.DimOrder o ON o.OrderKey = p.OrderKey
                       WHERE o.OrderId = :o""", o=oid)
        d = queries.order_detail(oid)
        assert Decimal(d["totals"]["ordered_value"]) == sum(Decimal(l["NetAmount"]) for l in lines)
        assert Decimal(d["totals"]["net_collections"]) == sum(Decimal(t["NetCollectionAmount"]) for t in txns)
        day = date.fromisoformat(d["order"]["order_date"])
        listed = queries.list_orders({"date_from": day, "date_to": day, "order_id": oid}, "order_id", "asc", 1, 50)
        item = next(i for i in listed["items"] if i["order_id"] == oid)
        assert item["ordered_value"] == d["totals"]["ordered_value"]
        assert item["net_collections"] == d["totals"]["net_collections"]
    # Whole-range totals from the order listing equal the fact-table totals.
    t = warehouse_totals()
    listing = queries.list_orders({"date_from": date(2025, 1, 1), "date_to": date(2026, 12, 31)}, "order_id", "asc", 1, 1)
    assert listing["totals"]["ordered_value"] == t["ordered_value"]
    assert listing["totals"]["net_collections"] == t["net_collections"]


# 10 -----------------------------------------------------------------------------------------
def test_historical_sales_prices_unchanged_after_catalog_price_update(fresh, settings):
    exp = scenario(fresh, "s06_catalog_price_update")["expected"]
    before = warehouse_totals()
    [res] = run_scenario("s06_catalog_price_update", settings)
    assert res["status"] == "succeeded"
    assert scalar("SELECT CurrentListPrice FROM dw.DimProduct WHERE ProductSku = 'SKU-00001'") == Decimal(exp["sku_00001_list_price"])
    stats = {r["entity"]: r for r in rows("SELECT * FROM meta.run_entity_stats WHERE run_id = :r", r=res["run_id"])}
    assert stats["product"]["rows_updated"] == exp["dim_product_updated"]
    d = queries.order_detail(1001)
    kettle = next(l for l in d["lines"] if l["sku"] == "SKU-00001")
    assert kettle["unit_price"] == "100.00" and kettle["current_list_price"] == "120.00"
    assert d["totals"]["gross_merchandise_value"] == exp["order_1001_gross_merchandise_value"]
    assert warehouse_totals() == before


# 11 -----------------------------------------------------------------------------------------
def test_cancelled_orders_follow_metric_definitions(fresh, settings):
    exp = scenario(fresh, "s03_order_updates")["expected"]
    rng = (date(2025, 1, 1), date(2026, 12, 31))
    before = queries.overview(*rng)["metrics"]
    run_scenario("s03_order_updates", settings)
    after = queries.overview(*rng)["metrics"]
    d = exp["warehouse_delta"]
    for k in ("ordered_value", "active_ordered_value", "gross_merchandise_value"):
        assert Decimal(after[k]["value"]) - Decimal(before[k]["value"]) == Decimal(d[k])
    assert after["distinct_active_orders"]["value"] - before["distinct_active_orders"]["value"] == d["distinct_active_orders"]
    # Cancelled lines are in ordered value but not in active ordered value.
    cancelled_value = scalar("""SELECT SUM(f.NetAmount) FROM dw.FactSalesLine f JOIN dw.DimOrder o ON o.OrderKey = f.OrderKey
                                WHERE o.OrderStatus = 'cancelled'""")
    assert Decimal(after["ordered_value"]["value"]) - Decimal(after["active_ordered_value"]["value"]) == cancelled_value
    aov = (Decimal(after["active_ordered_value"]["value"]) / after["distinct_active_orders"]["value"]).quantize(Decimal("0.01"))
    assert Decimal(after["average_order_value"]["value"]) == aov
