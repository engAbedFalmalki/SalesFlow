"""HTTP API: totals vs independent SQL (14), validation, safety, worker path."""

from __future__ import annotations

import csv
import io
import time
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from openpyxl.utils import get_column_letter

from salesflow import geo

from salesflow.api.app import app
from salesflow.config import get_settings
from salesflow.pipeline.worker import start_background_worker
from salesflow.scenario_runner import run_scenario

from helpers import rows, scalar, warehouse_totals

ALL = "date_from=2025-01-01&date_to=2026-12-31"


def wb_rows(ws):
    return [r for r in ws.iter_rows(values_only=True) if r and r[0]]


@pytest.fixture
def client():
    return TestClient(app)


# 14 -----------------------------------------------------------------------------------------
def test_api_totals_match_independent_sql(fresh, client):
    t = warehouse_totals()
    m = client.get(f"/api/overview?{ALL}").json()["metrics"]
    for k in ("gross_merchandise_value", "ordered_value", "active_ordered_value", "successful_collections",
              "successful_refunds", "net_collections", "distinct_active_orders"):
        assert m[k]["value"] == t[k], k
    assert m["average_order_value"]["value"] == f"{Decimal(t['active_ordered_value']) / t['distinct_active_orders']:.2f}"

    oct_ = "date_from=2026-10-01&date_to=2026-10-31"
    sql_oct = rows("""
        SELECT (SELECT SUM(f.NetAmount) FROM dw.FactSalesLine f JOIN dw.DimOrder o ON o.OrderKey = f.OrderKey
                JOIN dw.DimDate d ON d.DateKey = f.OrderDateKey
                WHERE o.OrderStatus <> 'cancelled' AND d.[Year] = 2026 AND d.[Month] = 10) AS active,
               (SELECT SUM(p.Amount) FROM dw.FactPaymentTransaction p
                WHERE p.TransactionType = 'payment' AND p.TransactionStatus = 'succeeded'
                  AND CAST(DATEADD(HOUR, 3, p.TransactionAtUtc) AS DATE) BETWEEN '2026-10-01' AND '2026-10-31') AS coll""")[0]
    m = client.get(f"/api/overview?{oct_}").json()["metrics"]
    assert m["active_ordered_value"]["value"] == f"{sql_oct['active']:.2f}"
    assert m["successful_collections"]["value"] == f"{sql_oct['coll']:.2f}"

    ts = client.get(f"/api/timeseries?{oct_}&grain=day").json()["points"]
    assert len(ts) == 31
    assert f"{sum(Decimal(p['active_ordered_value']) for p in ts):.2f}" == m["active_ordered_value"]["value"]
    assert f"{sum(Decimal(p['net_collections']) for p in ts):.2f}" == m["net_collections"]["value"]
    weekly = client.get(f"/api/timeseries?{oct_}&grain=month").json()["points"]
    assert len(weekly) == 1 and weekly[0]["successful_collections"] == m["successful_collections"]["value"]

    top = client.get(f"/api/products/top?{oct_}&limit=50").json()
    cat_sum = sum(Decimal(c["active_ordered_value"]) for c in top["categories"])
    assert f"{cat_sum:.2f}" == m["active_ordered_value"]["value"]
    best = top["products"][0]
    sql_best = scalar("""SELECT SUM(f.NetAmount) FROM dw.FactSalesLine f JOIN dw.DimOrder o ON o.OrderKey = f.OrderKey
                         JOIN dw.DimProduct p ON p.ProductKey = f.ProductKey
                         WHERE p.ProductSku = :s AND o.IsCancelled = 0 AND f.OrderDateKey BETWEEN 20261001 AND 20261031""",
                      s=best["sku"])
    assert best["active_ordered_value"] == f"{sql_best:.2f}"

    dist = client.get(f"/api/orders/status-distribution?{oct_}").json()["items"]
    assert sum(d["orders"] for d in dist) == scalar("SELECT COUNT(*) FROM dw.DimOrder WHERE OrderDateKey BETWEEN 20261001 AND 20261031")


def test_order_listing_filters_sorting_pagination_and_export(fresh, client):
    base = "date_from=2026-09-01&date_to=2026-09-30"
    p1 = client.get(f"/api/orders?{base}&page_size=10&sort=ordered_value&direction=desc").json()
    assert len(p1["items"]) == 10 and p1["total"] == scalar(
        "SELECT COUNT(*) FROM dw.DimOrder WHERE OrderDateKey BETWEEN 20260901 AND 20260930")
    vals = [Decimal(i["ordered_value"]) for i in p1["items"]]
    assert vals == sorted(vals, reverse=True)
    p2 = client.get(f"/api/orders?{base}&page_size=10&page=2&sort=ordered_value&direction=desc").json()
    assert not {i["order_id"] for i in p1["items"]} & {i["order_id"] for i in p2["items"]}

    cancelled = client.get(f"/api/orders?{base}&status=cancelled&page_size=100").json()
    assert cancelled["total"] > 0 and all(i["status"] == "cancelled" for i in cancelled["items"])
    two = client.get(f"/api/orders?{base}&status=cancelled&status=pending&page_size=100").json()
    assert two["total"] >= cancelled["total"]

    cat = client.get("/api/filters").json()["categories"][0]
    by_cat = client.get(f"/api/orders?{base}&category={cat}&page_size=5").json()
    for i in by_cat["items"]:
        detail = client.get(f"/api/orders/{i['order_id']}").json()
        assert any(l["category"] == cat for l in detail["lines"])

    f = client.get("/api/filters").json()
    assert len(f["regions"]) == 13 and {c["region"] for c in f["cities"]} == {r["value"] for r in f["regions"]}
    assert scalar("SELECT COUNT(*) FROM dw.DimCustomer WHERE Region IS NULL") == 0  # every generated city is known
    by_region = client.get(f"/api/orders?{base}&region=Eastern%20Province&page_size=100").json()
    assert 0 < by_region["total"] < p1["total"] and by_region["total"] == scalar(
        """SELECT COUNT(*) FROM dw.DimOrder o JOIN dw.DimCustomer c ON c.CustomerKey = o.CustomerKey
           WHERE o.OrderDateKey BETWEEN 20260901 AND 20260930 AND c.Region = 'Eastern Province'""")
    eastern = {c["value"] for c in f["cities"] if c["region"] == "Eastern Province"}
    assert all(i["customer_region"] == "Eastern Province" and i["customer_city"] in eastern for i in by_region["items"])
    by_city = client.get(f"/api/orders?{base}&city=Dammam&page_size=100").json()
    assert 0 < by_city["total"] < by_region["total"] and {i["customer_city"] for i in by_city["items"]} == {"Dammam"}
    mismatch = client.get(f"/api/orders?{base}&region=Asir&city=Dammam").json()
    assert mismatch["total"] == 0

    found = client.get("/api/orders?date_from=2026-08-01&date_to=2026-08-01&order_id=%231001").json()
    assert [i["order_id"] for i in found["items"]] == [1001]

    r = client.get(f"/api/orders/export.csv?{base}&status=cancelled")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    data = list(csv.reader(io.StringIO(r.content.decode("utf-8-sig"))))
    assert data[0][0] == "Order" and len(data) - 1 == cancelled["total"]
    col = data[0].index("Ordered value (SAR)")
    assert f"{sum(Decimal(x[col]) for x in data[1:]):.2f}" == cancelled["totals"]["ordered_value"]


def test_input_validation_and_structured_errors(client, fresh):
    cases = [
        "/api/orders?date_from=2026-09-01&date_to=2026-09-30&page_size=1000",
        "/api/orders?date_from=2026-09-01&date_to=2026-09-30&sort=1;DROP TABLE x",
        "/api/orders?date_from=2026-09-01&date_to=2026-09-30&order_id=1 OR 1=1",
        "/api/orders?date_from=2026-09-01&date_to=2026-09-30&status=lost",
        "/api/overview?date_from=2026-10-31&date_to=2026-10-01",
        "/api/overview?date_from=2020-01-01&date_to=2026-10-01",
        "/api/overview?date_from=not-a-date&date_to=2026-10-01",
        "/api/quality/quarantine?error_code=x'--",
    ]
    for u in cases:
        r = client.get(u)
        assert r.status_code == 422, u
        assert r.json()["error"]["code"] == "VALIDATION_ERROR" and r.json()["error"]["message"]
    r = client.get("/api/orders/99999999")
    assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"
    assert scalar("SELECT COUNT(*) FROM dw.DimOrder") > 0  # nothing was dropped


def test_mutations_are_protected(client, fresh, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "read_only", True)
    r = client.post("/api/pipeline/runs", json={"mode": "incremental"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "READ_ONLY"
    monkeypatch.setattr(s, "read_only", False)
    from pydantic import SecretStr
    monkeypatch.setattr(s, "admin_token", SecretStr("s3cret"))
    assert client.post("/api/pipeline/runs", json={"mode": "incremental"}).status_code == 401
    assert client.post("/api/pipeline/runs", json={"mode": "incremental"},
                       headers={"X-SalesFlow-Admin-Token": "wrong"}).status_code == 401
    ok = client.post("/api/pipeline/runs", json={"mode": "incremental"}, headers={"X-SalesFlow-Admin-Token": "s3cret"})
    assert ok.status_code == 202
    dup = client.post("/api/pipeline/runs", json={"mode": "initial"}, headers={"X-SalesFlow-Admin-Token": "s3cret"})
    assert dup.status_code == 409 and dup.json()["error"]["details"]["run_id"] == ok.json()["run_id"]
    monkeypatch.setattr(s, "env", "production")
    monkeypatch.setattr(s, "admin_token", None)
    assert client.post("/api/pipeline/runs", json={"mode": "incremental"}).status_code == 403
    # leave no queued run behind
    from salesflow.db import get_engine
    from sqlalchemy import text
    with get_engine("dw").begin() as c:
        c.execute(text("UPDATE meta.pipeline_run SET status = 'failed', active_slot = NULL WHERE status = 'queued'"))


def test_api_enqueue_is_executed_by_background_worker(client, fresh, settings):
    thread, stop = start_background_worker(settings)
    try:
        r = client.post("/api/pipeline/runs", json={"mode": "incremental"})
        assert r.status_code == 202
        run_id = r.json()["run_id"]
        assert client.get(f"/api/pipeline/runs/{run_id}").json()["status"] in ("queued", "running", "succeeded")
        deadline = time.time() + 120
        while time.time() < deadline:
            run = client.get(f"/api/pipeline/runs/{run_id}").json()
            if run["status"] in ("succeeded", "failed"):
                break
            time.sleep(0.5)
        assert run["status"] == "succeeded"
        assert run["worker_id"].startswith("embedded:")
        assert [s["status"] for s in run["stages"]] == ["succeeded"] * 7
    finally:
        stop.set()
        thread.join(timeout=10)


def test_quality_reconciliation_and_review_endpoints(client, fresh, settings):
    run_scenario("s08_invalid_values", settings)
    q = client.get("/api/quality").json()
    assert q["checks_failed"] > 0 and q["checks_passed"] > 0
    assert any(c["check_code"] == "INVALID_QUANTITY" and c["failed_count"] == 2 for c in q["checks"])
    lst = client.get("/api/quality/quarantine?status=open&error_code=INVALID_QUANTITY").json()
    assert lst["total"] == 2
    qid = lst["items"][0]["quarantine_id"]
    detail = client.get(f"/api/quality/quarantine/{qid}").json()
    assert detail["errors"][0]["code"] == "INVALID_QUANTITY" and detail["payload"]
    r = client.patch(f"/api/quality/quarantine/{qid}", json={"status": "ignored", "note": "test"})
    assert r.status_code == 200 and r.json()["status"] == "ignored"
    assert client.patch(f"/api/quality/quarantine/{qid}", json={"status": "ignored"}).status_code == 404
    rec = client.get("/api/reconciliation").json()
    states = {x["state"] for x in rec["results"]}
    assert "unresolved" not in states and "explained" in states and "informational" in states
    runs = client.get("/api/pipeline/runs?page_size=5").json()
    assert runs["total"] >= 2 and runs["items"][0]["run_id"] > runs["items"][-1]["run_id"]
    fr = client.get("/api/freshness").json()
    assert fr["last_successful_run"]["run_id"] == runs["items"][0]["run_id"]
    assert fr["open_quarantine_records"] == scalar("SELECT COUNT(*) FROM qa.quarantine_record WHERE status = 'open'")
    d = client.get("/api/definitions").json()
    assert {m["key"] for m in d["metrics"]} >= {"net_collections", "average_order_value"}
    assert "SYNTHETIC" in d["dataset"]["notice"]


def test_excel_and_csv_exports_are_clean_and_match_the_listing(fresh, client):
    from openpyxl import load_workbook
    base = "date_from=2026-09-01&date_to=2026-09-30&status=cancelled"
    listing = client.get(f"/api/orders?{base}&page_size=100").json()
    for lang in ("en", "ar"):
        r = client.get(f"/api/orders/export.xlsx?{base}&lang={lang}")
        assert r.status_code == 200 and r.headers["content-type"].startswith("application/vnd.openxmlformats")
        wb = load_workbook(io.BytesIO(r.content))
        ws = wb.worksheets[0]
        assert ws.freeze_panes == "A2" and ws.auto_filter.ref
        assert ws.sheet_view.rightToLeft is (lang == "ar")
        ar = lang == "ar"
        header = [c.value for c in ws[1]]
        assert header[0] == ("رقم الطلب" if ar else "Order")
        data = [row for row in ws.iter_rows(min_row=2, values_only=True) if isinstance(row[0], int)]
        assert len(data) == listing["total"]
        ordered_col = header.index("قيمة الطلب (ريال)" if ar else "Ordered value (SAR)")
        assert f"{sum(Decimal(str(r[ordered_col])) for r in data):.2f}" == listing["totals"]["ordered_value"]
        assert ws.cell(row=2, column=ordered_col + 1).number_format == "#,##0.00"
        assert {r[3] for r in data} == ({"ملغى"} if ar else {"Cancelled"})
        letter = get_column_letter(ordered_col + 1)
        assert any(isinstance(v, str) and v.startswith(f"=SUBTOTAL(9,{letter}2:") for row in ws.iter_rows(values_only=True) for v in row)
        region_col, city_col = header.index("المنطقة" if ar else "Region"), header.index("المدينة" if ar else "City")
        by_id = {i["order_id"]: i for i in listing["items"]}
        for row in data:
            src = by_id[row[0]]
            assert row[region_col] == geo.label(src["customer_region"], lang)
            assert row[city_col] == geo.label(src["customer_city"], lang)
        if ar:
            q = "date_from=2026-09-01&date_to=2026-09-30&region=Eastern%20Province&lang=ar"
            ws2 = load_workbook(io.BytesIO(client.get(f"/api/orders/export.xlsx?{q}").content)).worksheets[0]
            assert {r[region_col] for r in ws2.iter_rows(min_row=2, values_only=True) if isinstance(r[0], int)} == {"المنطقة الشرقية"}
            about = {r[0]: r[1] for r in wb_rows(ws2.parent.worksheets[1])}
            assert about["المنطقة"] == "المنطقة الشرقية" and about["المدينة"] == "الكل"
        assert wb.worksheets[1]["A1"].value.startswith("SalesFlow")
    r = client.get(f"/api/orders/export.csv?{base}&lang=ar")
    assert r.content.startswith(b"\xef\xbb\xbf")  # BOM: Excel opens Arabic names correctly
    rows = list(csv.reader(io.StringIO(r.content.decode("utf-8-sig"))))
    assert rows[0][0] == "رقم الطلب" and len(rows) - 1 == listing["total"]
    assert all(len(x[2]) == 16 for x in rows[1:])  # "YYYY-MM-DD HH:MM" Riyadh local

    # No matching orders: both files still carry their header row.
    empty = "date_from=2026-09-01&date_to=2026-09-30&order_id=999999999"
    assert client.get(f"/api/orders?{empty}").json()["total"] == 0
    rows = list(csv.reader(io.StringIO(client.get(f"/api/orders/export.csv?{empty}").content.decode("utf-8-sig"))))
    assert rows[0][:2] == ["Order", "Order date"] and len(rows) == 1
    ws = load_workbook(io.BytesIO(client.get(f"/api/orders/export.xlsx?{empty}").content)).worksheets[0]
    assert ws["A1"].value == "Order" and ws.max_row <= 2


def test_manual_purchase_flows_through_the_pipeline(fresh, client, settings):
    from salesflow.pipeline.executor import run_now
    sku, price = rows("SELECT TOP 1 product_sku, list_price FROM stg.product WHERE is_active = 1 ORDER BY product_sku")[0].values()
    bad = client.post("/api/dev/purchases", json={"customer_id": 1, "new_customer": {"first_name": "A", "last_name": "B",
                      "email": "a@b.c"}, "lines": [{"sku": sku, "quantity": 1}]})
    assert bad.status_code == 422
    assert client.post("/api/dev/purchases", json={"customer_id": 1, "lines": [{"sku": "NOPE-1", "quantity": 1}],
                                                   "run_pipeline": False}).status_code == 404
    r = client.post("/api/dev/purchases", json={
        "new_customer": {"first_name": "Test", "last_name": "Buyer", "email": "test.buyer@example.com", "city": "Riyadh"},
        "lines": [{"sku": sku, "quantity": 2, "discount_amount": "1.00"}], "channel": "app",
        "payment_method": "mada", "payment_outcome": "failed_then_paid", "run_pipeline": False})
    assert r.status_code == 201, r.text
    p = r.json()
    assert p["new_customer"] and p["ordered_value"] == f"{Decimal(price) * 2 - 1:.2f}" and len(p["transactions"]) == 2
    run_id, status = run_now("incremental", "test", settings=settings)
    assert status == "succeeded"
    d = client.get(f"/api/orders/{p['order_id']}").json()
    assert d["totals"]["ordered_value"] == p["ordered_value"]
    assert d["totals"]["successful_collections"] == p["ordered_value"]
    assert [t["status"] for t in d["transactions"]] == ["failed", "succeeded"]
    assert d["order"]["customer_name"] == "Test Buyer"
    # with run_pipeline the API queues the run for the worker
    r2 = client.post("/api/dev/purchases", json={"customer_id": 1, "lines": [{"sku": sku, "quantity": 1}],
                                                 "payment_method": "cash_on_delivery", "payment_outcome": "unpaid"})
    assert r2.status_code == 201 and r2.json()["run_id"] and r2.json()["payment_file"] is None
    from salesflow.db import get_engine
    from sqlalchemy import text
    with get_engine("dw").begin() as c:
        c.execute(text("UPDATE meta.pipeline_run SET status = 'failed', active_slot = NULL WHERE status = 'queued'"))


def test_arabic_texts(fresh, client, settings):
    run_scenario("s08_invalid_values", settings)
    d = client.get("/api/definitions?lang=ar").json()
    assert d["metrics"][0]["name"].startswith("إجمالي") and "بيانات اصطناعية" in d["dataset"]["notice"]
    assert client.get("/api/definitions?lang=fr").status_code == 422
    q = client.get("/api/quality/quarantine?status=open&page_size=100").json()["items"]
    assert q and all(i["error_reason_ar"] for i in q)
    detail = client.get(f"/api/quality/quarantine/{q[0]['quarantine_id']}").json()
    assert all(e.get("message_ar") for e in detail["errors"])
