"""Warehouse analytics and operational read queries (parameterised SQL only).

Rules enforced here:
* Sales measures come from FactSalesLine filtered by OrderDateKey.
* Collection measures come from FactPaymentTransaction filtered by TransactionDateKey.
* When both appear together they are aggregated to a common grain (order or
  date bucket) in separate CTEs before joining; never line x transaction.
* Money is returned as decimal strings, timestamps as ISO-8601 UTC ('Z').
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Iterator

from sqlalchemy import text
from sqlalchemy.engine import Connection

from .. import geo
from ..db import get_engine
from ..timeutil import date_key, iso_utc

CENT = Decimal("0.01")
ZERO = Decimal("0.00")


class NotFound(Exception):
    pass


def _v(v: Any) -> Any:
    if isinstance(v, Decimal):
        return format(v, "f")  # exact value at the column's SQL scale, e.g. '100.00'
    if isinstance(v, datetime):
        return iso_utc(v)
    if isinstance(v, date):
        return v.isoformat()
    return v


def _rows(conn: Connection, sql: str, **p: Any) -> list[dict]:
    return [{k: _v(v) for k, v in r._mapping.items()} for r in conn.execute(text(sql), p)]


def _one(conn: Connection, sql: str, **p: Any) -> dict | None:
    r = conn.execute(text(sql), p).first()
    return {k: _v(v) for k, v in r._mapping.items()} if r else None


def _conn():
    return get_engine("dw").connect()


def _money(d: Decimal | None) -> str | None:
    return None if d is None else f"{Decimal(d).quantize(CENT, rounding=ROUND_HALF_UP):.2f}"


# --------------------------------------------------------------------------- coverage

def data_coverage(conn: Connection | None = None) -> dict:
    own = conn is None
    conn = conn or _conn()
    try:
        r = conn.execute(text(
            """SELECT (SELECT MIN(d.[Date]) FROM dw.DimOrder o JOIN dw.DimDate d ON d.DateKey = o.OrderDateKey) AS min_order,
                      (SELECT MAX(d.[Date]) FROM dw.DimOrder o JOIN dw.DimDate d ON d.DateKey = o.OrderDateKey) AS max_order,
                      (SELECT MIN(d.[Date]) FROM dw.FactPaymentTransaction p JOIN dw.DimDate d ON d.DateKey = p.TransactionDateKey) AS min_txn,
                      (SELECT MAX(d.[Date]) FROM dw.FactPaymentTransaction p JOIN dw.DimDate d ON d.DateKey = p.TransactionDateKey) AS max_txn""")).first()
        dates = [d for d in r if d is not None]
        return {"min_order_date": _v(r.min_order), "max_order_date": _v(r.max_order),
                "min_transaction_date": _v(r.min_txn), "max_transaction_date": _v(r.max_txn),
                "min_date": _v(min(dates)) if dates else None, "max_date": _v(max(dates)) if dates else None}
    finally:
        if own:
            conn.close()


# --------------------------------------------------------------------------- overview

def _sales_metrics(conn: Connection, a: date, b: date) -> dict:
    r = conn.execute(text(
        """SELECT ISNULL(SUM(f.GrossAmount), 0) AS gmv, ISNULL(SUM(f.NetAmount), 0) AS ordered,
                  ISNULL(SUM(CASE WHEN o.IsCancelled = 0 THEN f.NetAmount END), 0) AS active,
                  COUNT(DISTINCT CASE WHEN o.IsCancelled = 0 THEN f.OrderKey END) AS active_orders,
                  COUNT(DISTINCT CASE WHEN o.IsCancelled = 1 THEN f.OrderKey END) AS cancelled_orders,
                  ISNULL(SUM(CASE WHEN o.IsCancelled = 0 THEN f.Quantity END), 0) AS units
           FROM dw.FactSalesLine f JOIN dw.DimOrder o ON o.OrderKey = f.OrderKey
           WHERE f.OrderDateKey BETWEEN :a AND :b"""), {"a": date_key(a), "b": date_key(b)}).first()
    aov = (r.active / r.active_orders).quantize(CENT, rounding=ROUND_HALF_UP) if r.active_orders else None
    return {"gross_merchandise_value": r.gmv, "ordered_value": r.ordered, "active_ordered_value": r.active,
            "distinct_active_orders": r.active_orders, "cancelled_orders": r.cancelled_orders,
            "units": r.units, "average_order_value": aov}


def _collection_metrics(conn: Connection, a: date, b: date) -> dict:
    r = conn.execute(text(
        """SELECT ISNULL(SUM(CASE WHEN TransactionType = 'payment' AND TransactionStatus = 'succeeded' THEN Amount END), 0) AS coll,
                  ISNULL(SUM(CASE WHEN TransactionType = 'refund' AND TransactionStatus = 'succeeded' THEN Amount END), 0) AS refunds,
                  SUM(CASE WHEN TransactionType = 'payment' AND TransactionStatus = 'failed' THEN 1 ELSE 0 END) AS failed_payments,
                  COUNT(*) AS txns
           FROM dw.FactPaymentTransaction WHERE TransactionDateKey BETWEEN :a AND :b"""),
        {"a": date_key(a), "b": date_key(b)}).first()
    return {"successful_collections": r.coll, "successful_refunds": r.refunds,
            "net_collections": r.coll - r.refunds, "failed_payment_attempts": r.failed_payments or 0}


def _compare(cur: dict, prev: dict | None) -> dict:
    out = {}
    for k, v in cur.items():
        p = None if prev is None else prev.get(k)
        entry: dict[str, Any] = {"value": _money(v) if isinstance(v, Decimal) else v,
                                 "previous": None if p is None else (_money(p) if isinstance(p, Decimal) else p),
                                 "change": None, "change_pct": None}
        if v is not None and p is not None:
            diff = Decimal(v) - Decimal(p)
            entry["change"] = _money(diff) if isinstance(v, Decimal) else int(diff)
            entry["change_pct"] = None if Decimal(p) == 0 else f"{(diff / Decimal(p) * 100).quantize(Decimal('0.1'))}"
        out[k] = entry
    return out


def overview(date_from: date, date_to: date) -> dict:
    days = (date_to - date_from).days + 1
    prev_to = date_from - timedelta(days=1)
    prev_from = prev_to - timedelta(days=days - 1)
    with _conn() as conn:
        cov = data_coverage(conn)
        cur = {**_sales_metrics(conn, date_from, date_to), **_collection_metrics(conn, date_from, date_to)}
        min_date = date.fromisoformat(cov["min_date"]) if cov["min_date"] else None
        available = min_date is not None and prev_from >= min_date
        prev = {**_sales_metrics(conn, prev_from, prev_to), **_collection_metrics(conn, prev_from, prev_to)} if available else None
    return {
        "period": {"date_from": date_from.isoformat(), "date_to": date_to.isoformat(), "days": days},
        "comparison": {"date_from": prev_from.isoformat(), "date_to": prev_to.isoformat(), "days": days,
                       "available": available,
                       "reason": None if available else
                       "The previous period of equal length starts before the first available data"
                       + (f" ({cov['min_date']})." if cov["min_date"] else ".")},
        "metrics": _compare(cur, prev),
        "coverage": cov,
    }


def timeseries(date_from: date, date_to: date, grain: str) -> dict:
    bucket = {"day": "d.[Date]", "week": "d.WeekStartDate", "month": "d.MonthStartDate"}[grain]
    sql = f"""
    WITH buckets AS (
        SELECT DISTINCT {bucket} AS bucket FROM dw.DimDate d WHERE d.DateKey BETWEEN :a AND :b),
    sales AS (
        SELECT {bucket} AS bucket,
               SUM(CASE WHEN o.IsCancelled = 0 THEN f.NetAmount ELSE 0 END) AS active_ordered_value,
               COUNT(DISTINCT CASE WHEN o.IsCancelled = 0 THEN f.OrderKey END) AS orders
        FROM dw.FactSalesLine f JOIN dw.DimOrder o ON o.OrderKey = f.OrderKey
        JOIN dw.DimDate d ON d.DateKey = f.OrderDateKey
        WHERE f.OrderDateKey BETWEEN :a AND :b GROUP BY {bucket}),
    coll AS (
        SELECT {bucket} AS bucket,
               SUM(CASE WHEN p.TransactionType = 'payment' AND p.TransactionStatus = 'succeeded' THEN p.Amount ELSE 0 END) AS successful_collections,
               SUM(CASE WHEN p.TransactionType = 'refund' AND p.TransactionStatus = 'succeeded' THEN p.Amount ELSE 0 END) AS successful_refunds
        FROM dw.FactPaymentTransaction p JOIN dw.DimDate d ON d.DateKey = p.TransactionDateKey
        WHERE p.TransactionDateKey BETWEEN :a AND :b GROUP BY {bucket})
    SELECT b.bucket, ISNULL(s.active_ordered_value, 0) AS active_ordered_value, ISNULL(s.orders, 0) AS orders,
           ISNULL(c.successful_collections, 0) AS successful_collections,
           ISNULL(c.successful_refunds, 0) AS successful_refunds,
           ISNULL(c.successful_collections, 0) - ISNULL(c.successful_refunds, 0) AS net_collections
    FROM buckets b LEFT JOIN sales s ON s.bucket = b.bucket LEFT JOIN coll c ON c.bucket = b.bucket
    ORDER BY b.bucket"""
    with _conn() as conn:
        points = _rows(conn, sql, a=date_key(date_from), b=date_key(date_to))
    return {"grain": grain, "date_from": date_from.isoformat(), "date_to": date_to.isoformat(),
            "series_date_basis": {"active_ordered_value": "order date", "orders": "order date",
                                  "successful_collections": "transaction date",
                                  "successful_refunds": "transaction date", "net_collections": "transaction date"},
            "points": points}


def top_products(date_from: date, date_to: date, category: str | None, limit: int) -> dict:
    with _conn() as conn:
        rows = _rows(conn, """
            WITH t AS (
                SELECT p.ProductSku, p.ProductName, p.Category, SUM(f.Quantity) AS units,
                       COUNT(DISTINCT f.OrderKey) AS orders, SUM(f.NetAmount) AS active_ordered_value
                FROM dw.FactSalesLine f JOIN dw.DimOrder o ON o.OrderKey = f.OrderKey
                JOIN dw.DimProduct p ON p.ProductKey = f.ProductKey
                WHERE f.OrderDateKey BETWEEN :a AND :b AND o.IsCancelled = 0
                  AND (:cat IS NULL OR p.Category = :cat)
                GROUP BY p.ProductSku, p.ProductName, p.Category)
            SELECT TOP (:n) ProductSku AS sku, ProductName AS name, Category AS category, units, orders,
                   active_ordered_value,
                   CAST(active_ordered_value * 100.0 / NULLIF(SUM(active_ordered_value) OVER (), 0) AS DECIMAL(9,2)) AS share_pct_of_all
            FROM t ORDER BY active_ordered_value DESC, ProductSku""",
                     a=date_key(date_from), b=date_key(date_to), cat=category, n=limit)
        cats = _rows(conn, """
            SELECT p.Category AS category, SUM(f.NetAmount) AS active_ordered_value, SUM(f.Quantity) AS units,
                   COUNT(DISTINCT f.OrderKey) AS orders
            FROM dw.FactSalesLine f JOIN dw.DimOrder o ON o.OrderKey = f.OrderKey
            JOIN dw.DimProduct p ON p.ProductKey = f.ProductKey
            WHERE f.OrderDateKey BETWEEN :a AND :b AND o.IsCancelled = 0
            GROUP BY p.Category ORDER BY SUM(f.NetAmount) DESC""", a=date_key(date_from), b=date_key(date_to))
    return {"products": rows, "categories": cats,
            "note": "Sales-line measures only (non-cancelled orders, order date). Payments are not allocated to products."}


def status_distribution(date_from: date, date_to: date) -> list[dict]:
    with _conn() as conn:
        return _rows(conn, """
            WITH lines AS (SELECT OrderKey, SUM(NetAmount) AS ordered FROM dw.FactSalesLine
                           WHERE OrderDateKey BETWEEN :a AND :b GROUP BY OrderKey)
            SELECT o.OrderStatus AS status, COUNT(*) AS orders, ISNULL(SUM(l.ordered), 0) AS ordered_value
            FROM dw.DimOrder o LEFT JOIN lines l ON l.OrderKey = o.OrderKey
            WHERE o.OrderDateKey BETWEEN :a AND :b
            GROUP BY o.OrderStatus ORDER BY COUNT(*) DESC""", a=date_key(date_from), b=date_key(date_to))


# --------------------------------------------------------------------------- freshness

def freshness() -> dict:
    with _conn() as conn:
        last_ok = _one(conn, """SELECT TOP 1 run_id, mode, started_at_utc, finished_at_utc,
                                  DATEDIFF(MINUTE, finished_at_utc, SYSUTCDATETIME()) AS minutes_ago
                                FROM meta.pipeline_run WHERE status = 'succeeded' ORDER BY finished_at_utc DESC""")
        last_any = _one(conn, """SELECT TOP 1 run_id, mode, status, requested_at_utc, finished_at_utc, error_code
                                 FROM meta.pipeline_run ORDER BY run_id DESC""")
        active = _one(conn, "SELECT TOP 1 run_id, mode, status, current_stage FROM meta.pipeline_run WHERE active_slot = 1")
        marks = _rows(conn, "SELECT source_name, watermark_utc, last_run_id FROM meta.watermark ORDER BY source_name")
        files = _rows(conn, """SELECT source_system, MAX(received_at_utc) AS last_received_at_utc,
                                      SUM(CASE WHEN status = 'processed' THEN 1 ELSE 0 END) AS processed_files,
                                      SUM(CASE WHEN status IN ('rejected','conflict') THEN 1 ELSE 0 END) AS problem_files,
                                      MAX(CASE WHEN status = 'processed' THEN file_name END) AS latest_processed_file
                               FROM meta.source_file GROUP BY source_system""")
        open_q = conn.execute(text("SELECT COUNT(*) FROM qa.quarantine_record WHERE status = 'open'")).scalar()
        cov = data_coverage(conn)
    return {"last_successful_run": last_ok, "last_run": last_any, "active_run": active, "watermarks": marks,
            "files": files, "open_quarantine_records": open_q, "coverage": cov,
            "server_time_utc": iso_utc(datetime.now(timezone.utc))}


# --------------------------------------------------------------------------- sales explorer

ORDER_SORTS = {
    "order_date": "o.OrderPlacedAtUtc", "order_id": "o.OrderId", "ordered_value": "ordered_value",
    "net_collections": "net_collections", "status": "o.OrderStatus", "customer": "c.FullName",
}


def _order_filters(f: dict) -> tuple[str, dict]:
    where = ["o.OrderDateKey BETWEEN :a AND :b"]
    p: dict[str, Any] = {"a": date_key(f["date_from"]), "b": date_key(f["date_to"])}
    if f.get("statuses"):
        where.append("o.OrderStatus IN (SELECT value FROM OPENJSON(:statuses))")
        p["statuses"] = json.dumps(f["statuses"])
    if f.get("category"):
        where.append("EXISTS (SELECT 1 FROM dw.FactSalesLine x JOIN dw.DimProduct xp ON xp.ProductKey = x.ProductKey "
                     "WHERE x.OrderKey = o.OrderKey AND xp.Category = :cat)")
        p["cat"] = f["category"]
    if f.get("product_sku"):
        where.append("EXISTS (SELECT 1 FROM dw.FactSalesLine x JOIN dw.DimProduct xp ON xp.ProductKey = x.ProductKey "
                     "WHERE x.OrderKey = o.OrderKey AND xp.ProductSku = :sku)")
        p["sku"] = f["product_sku"]
    if f.get("region"):
        where.append("o.CustomerKey IN (SELECT CustomerKey FROM dw.DimCustomer WHERE Region = :region)")
        p["region"] = f["region"]
    if f.get("city"):
        where.append("o.CustomerKey IN (SELECT CustomerKey FROM dw.DimCustomer WHERE City = :city)")
        p["city"] = f["city"]
    if f.get("order_id") is not None:
        where.append("CAST(o.OrderId AS VARCHAR(12)) LIKE :oid")
        p["oid"] = f"{f['order_id']}%"
    return " AND ".join(where), p


_ORDER_SELECT = """
WITH base AS (
    SELECT o.OrderKey FROM dw.DimOrder o WHERE {where}),
lines AS (
    SELECT f.OrderKey, COUNT(*) AS line_count, SUM(f.Quantity) AS units, SUM(f.GrossAmount) AS gmv,
           SUM(f.NetAmount) AS ordered_value
    FROM dw.FactSalesLine f JOIN base b ON b.OrderKey = f.OrderKey GROUP BY f.OrderKey),
pay AS (
    SELECT p.OrderKey,
           SUM(CASE WHEN p.TransactionType = 'payment' AND p.TransactionStatus = 'succeeded' THEN p.Amount ELSE 0 END) AS collections,
           SUM(CASE WHEN p.TransactionType = 'refund' AND p.TransactionStatus = 'succeeded' THEN p.Amount ELSE 0 END) AS refunds,
           COUNT(*) AS transactions
    FROM dw.FactPaymentTransaction p JOIN base b ON b.OrderKey = p.OrderKey GROUP BY p.OrderKey),
o2 AS (
    SELECT o.OrderId AS order_id, o.OrderPlacedAtUtc AS order_placed_at_utc, d.[Date] AS order_date,
           o.OrderStatus AS status, o.Channel AS channel, c.CustomerCode AS customer_code, c.FullName AS customer_name,
           c.Region AS customer_region, c.City AS customer_city,
           ISNULL(l.line_count, 0) AS line_count, ISNULL(l.units, 0) AS units,
           ISNULL(l.gmv, 0) AS gross_merchandise_value, ISNULL(l.ordered_value, 0) AS ordered_value,
           ISNULL(p.collections, 0) AS successful_collections, ISNULL(p.refunds, 0) AS successful_refunds,
           ISNULL(p.collections, 0) - ISNULL(p.refunds, 0) AS net_collections, ISNULL(p.transactions, 0) AS transactions,
           o.OrderPlacedAtUtc AS _placed, o.OrderId AS _oid, c.FullName AS _cust
    FROM base b JOIN dw.DimOrder o ON o.OrderKey = b.OrderKey
    JOIN dw.DimCustomer c ON c.CustomerKey = o.CustomerKey
    JOIN dw.DimDate d ON d.DateKey = o.OrderDateKey
    LEFT JOIN lines l ON l.OrderKey = o.OrderKey
    LEFT JOIN pay p ON p.OrderKey = o.OrderKey)
"""


def _collection_state(r: dict) -> str:
    coll, ref = Decimal(r["successful_collections"]), Decimal(r["successful_refunds"])
    if coll == 0:
        return "unpaid"
    if coll - ref <= 0:
        return "refunded"
    if ref > 0:
        return "partially_refunded"
    if coll < Decimal(r["ordered_value"]):
        return "partially_paid"
    return "paid"


def list_orders(f: dict, sort: str, direction: str, page: int, page_size: int) -> dict:
    where, p = _order_filters(f)
    order_col = {"order_date": "_placed", "order_id": "_oid", "ordered_value": "ordered_value",
                 "net_collections": "net_collections", "status": "status", "customer": "_cust"}[sort]
    dir_sql = "DESC" if direction == "desc" else "ASC"
    tiebreak = "" if order_col == "_oid" else f", _oid {dir_sql}"  # deterministic paging
    sql = _ORDER_SELECT.format(where=where) + f"""
    SELECT order_id, order_placed_at_utc, order_date, status, channel, customer_code, customer_name,
           customer_region, customer_city, line_count, units,
           gross_merchandise_value, ordered_value, successful_collections, successful_refunds, net_collections, transactions
    FROM o2 ORDER BY {order_col} {dir_sql}{tiebreak}
    OFFSET :off ROWS FETCH NEXT :n ROWS ONLY"""
    totals_sql = _ORDER_SELECT.format(where=where) + """
    SELECT COUNT(*) AS orders, ISNULL(SUM(ordered_value), 0) AS ordered_value,
           ISNULL(SUM(CASE WHEN status <> 'cancelled' THEN ordered_value ELSE 0 END), 0) AS active_ordered_value,
           ISNULL(SUM(net_collections), 0) AS net_collections FROM o2"""
    with _conn() as conn:
        items = _rows(conn, sql, **p, off=(page - 1) * page_size, n=page_size)
        totals = _one(conn, totals_sql, **p)
    for it in items:
        it["collection_state"] = _collection_state(it)
    return {"items": items, "page": page, "page_size": page_size, "total": totals["orders"], "totals": totals,
            "note": "Orders are selected by order date (Asia/Riyadh). Collection columns are lifetime totals for each "
                    "order, regardless of transaction date."}


def iter_order_rows(f: dict, limit: int) -> Iterator[dict]:
    """Filtered orders at order grain with raw types (Decimal, date, naive-UTC datetime) for exports."""
    where, p = _order_filters(f)
    sql = _ORDER_SELECT.format(where=where) + """
    SELECT TOP (:n) order_id, order_date, order_placed_at_utc, status, channel, customer_code, customer_name,
           customer_region, customer_city, line_count, units, gross_merchandise_value, ordered_value, successful_collections, successful_refunds,
           net_collections, transactions
    FROM o2 ORDER BY _placed, _oid"""
    with _conn() as conn:
        for r in conn.execute(text(sql), {**p, "n": limit}):
            row = dict(r._mapping)
            row["discounts"] = row["gross_merchandise_value"] - row["ordered_value"]
            row["collection_state"] = _collection_state({k: str(v) for k, v in row.items()})
            yield row


def order_detail(order_id: int) -> dict:
    with _conn() as conn:
        head = _one(conn, """
            SELECT o.OrderId AS order_id, o.OrderPlacedAtUtc AS order_placed_at_utc, d.[Date] AS order_date,
                   o.OrderStatus AS status, o.Channel AS channel, c.CustomerCode AS customer_code,
                   c.FullName AS customer_name, c.City AS customer_city, c.Region AS customer_region, o.SourceUpdatedAtUtc AS source_updated_at_utc,
                   o.UpdatedRunId AS updated_run_id
            FROM dw.DimOrder o JOIN dw.DimCustomer c ON c.CustomerKey = o.CustomerKey
            JOIN dw.DimDate d ON d.DateKey = o.OrderDateKey WHERE o.OrderId = :id""", id=order_id)
        if head is None:
            raise NotFound(f"Order {order_id} is not in the warehouse.")
        lines = _rows(conn, """
            SELECT f.OrderItemId AS order_item_id, f.LineNumber AS line_number, p.ProductSku AS sku,
                   p.ProductName AS product_name, p.Category AS category, f.Quantity AS quantity,
                   f.UnitPrice AS unit_price, p.CurrentListPrice AS current_list_price,
                   f.DiscountAmount AS discount_amount, f.GrossAmount AS gross_amount, f.NetAmount AS net_amount
            FROM dw.FactSalesLine f JOIN dw.DimOrder o ON o.OrderKey = f.OrderKey
            JOIN dw.DimProduct p ON p.ProductKey = f.ProductKey WHERE o.OrderId = :id ORDER BY f.LineNumber""", id=order_id)
        txns = _rows(conn, """
            SELECT t.TransactionId AS transaction_id, t.TransactionType AS transaction_type,
                   t.TransactionStatus AS status, t.Amount AS amount, t.PaymentMethod AS payment_method,
                   t.TransactionAtUtc AS transaction_at_utc, d.[Date] AS transaction_date,
                   t.RefersToTransactionId AS refers_to_transaction_id, f.file_name AS source_file
            FROM dw.FactPaymentTransaction t JOIN dw.DimOrder o ON o.OrderKey = t.OrderKey
            JOIN dw.DimDate d ON d.DateKey = t.TransactionDateKey
            LEFT JOIN meta.source_file f ON f.file_id = t.SourceFileId
            WHERE o.OrderId = :id ORDER BY t.TransactionAtUtc, t.TransactionId""", id=order_id)
        quarantined = _rows(conn, """
            SELECT quarantine_id, entity, source_record_id, error_code, error_reason, error_reason_ar, status
            FROM qa.quarantine_record
            WHERE status IN ('open','ignored') AND (
                  (entity = 'order' AND source_record_id = CAST(:id AS NVARCHAR(100)))
               OR (entity IN ('order_item','payment_txn') AND TRY_CAST(CASE WHEN ISJSON(payload) = 1 THEN JSON_VALUE(payload, '$.order_id') END AS INT) = :id))""",
                           id=order_id)
    gmv = sum((Decimal(l["gross_amount"]) for l in lines), ZERO)
    ordered = sum((Decimal(l["net_amount"]) for l in lines), ZERO)
    coll = sum((Decimal(t["amount"]) for t in txns if t["transaction_type"] == "payment" and t["status"] == "succeeded"), ZERO)
    ref = sum((Decimal(t["amount"]) for t in txns if t["transaction_type"] == "refund" and t["status"] == "succeeded"), ZERO)
    totals = {"gross_merchandise_value": _money(gmv), "discounts": _money(gmv - ordered), "ordered_value": _money(ordered),
              "successful_collections": _money(coll), "successful_refunds": _money(ref),
              "net_collections": _money(coll - ref)}
    totals["collection_state"] = _collection_state({"successful_collections": totals["successful_collections"],
                                                     "successful_refunds": totals["successful_refunds"],
                                                     "ordered_value": totals["ordered_value"]})
    return {"order": head, "lines": lines, "transactions": txns, "totals": totals, "quarantined_records": quarantined}


def filter_options() -> dict:
    with _conn() as conn:
        cats = [r[0] for r in conn.execute(text("SELECT DISTINCT Category FROM dw.DimProduct ORDER BY Category"))]
        prods = _rows(conn, """SELECT ProductSku AS sku, ProductName AS name, Category AS category,
                                      CurrentListPrice AS list_price, IsActive AS is_active
                               FROM dw.DimProduct ORDER BY ProductSku""")
        cov = data_coverage(conn)
    return {"categories": cats, "products": prods, **geo.reference(),
            "statuses": ["pending", "processing", "shipped", "delivered", "cancelled"], "coverage": cov}


# --------------------------------------------------------------------------- pipeline runs

_RUN_COLS = """r.run_id, r.mode, r.status, r.requested_by, r.params_json, r.retry_of_run_id, r.requested_at_utc,
               r.started_at_utc, r.finished_at_utc, r.heartbeat_at_utc, r.worker_id, r.current_stage, r.error_stage,
               r.error_code, r.error_message,
               CAST(DATEDIFF(MILLISECOND, r.started_at_utc, ISNULL(r.finished_at_utc, SYSUTCDATETIME())) / 1000.0 AS DECIMAL(12,1)) AS duration_seconds,
               (SELECT COUNT(*) FROM meta.pipeline_run_stage s WHERE s.run_id = r.run_id AND s.status = 'succeeded') AS stages_succeeded,
               (SELECT COUNT(*) FROM meta.pipeline_run_stage s WHERE s.run_id = r.run_id AND s.status <> 'skipped') AS stages_total"""


def _shape_run(r: dict) -> dict:
    r["params"] = json.loads(r.pop("params_json") or "{}")
    stage, code, msg = r.pop("error_stage"), r.pop("error_code"), r.pop("error_message")
    r["error"] = {"stage": stage, "code": code, "message": msg} if code else None
    return r


def list_runs(page: int = 1, page_size: int = 20, status: str | None = None) -> dict:
    with _conn() as conn:
        items = _rows(conn, f"""SELECT {_RUN_COLS} FROM meta.pipeline_run r
                                WHERE (:st IS NULL OR r.status = :st)
                                ORDER BY r.run_id DESC OFFSET :o ROWS FETCH NEXT :n ROWS ONLY""",
                      st=status, o=(page - 1) * page_size, n=page_size)
        total = conn.execute(text("SELECT COUNT(*) FROM meta.pipeline_run WHERE (:st IS NULL OR status = :st)"),
                             {"st": status}).scalar()
    return {"items": [_shape_run(r) for r in items], "page": page, "page_size": page_size, "total": total}


def get_run(run_id: int) -> dict:
    with _conn() as conn:
        r = _one(conn, f"SELECT {_RUN_COLS}, r.summary_json FROM meta.pipeline_run r WHERE r.run_id = :id", id=run_id)
        if r is None:
            raise NotFound(f"Run {run_id} does not exist.")
        stages = _rows(conn, """SELECT stage_seq, stage_name, status, started_at_utc, finished_at_utc,
                                    CAST(DATEDIFF(MILLISECOND, started_at_utc, ISNULL(finished_at_utc, SYSUTCDATETIME())) / 1000.0 AS DECIMAL(12,1)) AS duration_seconds,
                                    rows_read, rows_written, rows_rejected, message, details_json
                                FROM meta.pipeline_run_stage WHERE run_id = :id ORDER BY stage_seq""", id=run_id)
        stats = _rows(conn, "SELECT * FROM meta.run_entity_stats WHERE run_id = :id ORDER BY entity", id=run_id)
        files = _rows(conn, """SELECT file_id, source_system, file_name, status, record_count, file_size_bytes,
                                   file_checksum, duplicate_of_file_id, message, message_ar, received_at_utc, processed_run_id
                               FROM meta.source_file WHERE first_seen_run_id = :id OR processed_run_id = :id
                               ORDER BY file_id""", id=run_id)
        retries = [x[0] for x in conn.execute(text("SELECT run_id FROM meta.pipeline_run WHERE retry_of_run_id = :id"),
                                              {"id": run_id})]
        quarantined = conn.execute(text("SELECT COUNT(*) FROM qa.quarantine_record WHERE run_id = :id"), {"id": run_id}).scalar()
    from ..pipeline.runs import STAGES
    labels = dict(STAGES)
    for s in stages:
        s["label"] = labels.get(s["stage_name"], s["stage_name"])
        s["details"] = json.loads(s.pop("details_json")) if s.get("details_json") else None
    summary = json.loads(r.pop("summary_json")) if r.get("summary_json") else None
    out = _shape_run(r)
    out.update(stages=stages, entity_stats=stats, files=files, summary=summary, retried_by=retries,
               quarantined_in_run=quarantined)
    return out


# --------------------------------------------------------------------------- data quality

def quality_summary(run_id: int | None) -> dict:
    with _conn() as conn:
        if run_id is None:
            run_id = conn.execute(text("SELECT MAX(run_id) FROM qa.check_result")).scalar()
        checks = _rows(conn, """SELECT check_code, check_name, check_level, entity, severity, status, checked_count,
                                       failed_count, details FROM qa.check_result WHERE run_id = :r
                                ORDER BY CASE status WHEN 'failed' THEN 0 ELSE 1 END, check_level, entity, check_code""",
                       r=run_id) if run_id else []
        by_code = _rows(conn, """SELECT error_code, entity, status, COUNT(*) AS records FROM qa.quarantine_record
                                 GROUP BY error_code, entity, status ORDER BY COUNT(*) DESC""")
        by_status = _rows(conn, "SELECT status, COUNT(*) AS records FROM qa.quarantine_record GROUP BY status")
    passed = sum(1 for c in checks if c["status"] == "passed")
    return {"run_id": run_id, "checks": checks, "checks_passed": passed, "checks_failed": len(checks) - passed,
            "quarantine_by_code": by_code, "quarantine_by_status": by_status}


def list_quarantine(f: dict, page: int, page_size: int) -> dict:
    where = ["1 = 1"]
    p: dict[str, Any] = {}
    for col in ("entity", "error_code", "status", "run_id"):
        if f.get(col) is not None:
            where.append(f"q.{col} = :{col}")
            p[col] = f[col]
    if f.get("q"):
        where.append("q.source_record_id LIKE :q")
        p["q"] = f"%{f['q']}%"
    w = " AND ".join(where)
    with _conn() as conn:
        items = _rows(conn, f"""SELECT q.quarantine_id, q.entity, q.source_system, q.source_record_id, q.source_file_name,
                                       q.run_id, q.error_code, q.error_reason, q.error_reason_ar, q.status, q.evaluation_count,
                                       q.last_evaluated_run_id, q.resolved_run_id, q.created_at_utc, q.updated_at_utc
                                FROM qa.quarantine_record q WHERE {w}
                                ORDER BY q.quarantine_id DESC OFFSET :o ROWS FETCH NEXT :n ROWS ONLY""",
                      **p, o=(page - 1) * page_size, n=page_size)
        total = conn.execute(text(f"SELECT COUNT(*) FROM qa.quarantine_record q WHERE {w}"), p).scalar()
        codes = [r[0] for r in conn.execute(text("SELECT DISTINCT error_code FROM qa.quarantine_record ORDER BY 1"))]
    return {"items": items, "page": page, "page_size": page_size, "total": total, "error_codes": codes}


def quarantine_detail(qid: int) -> dict:
    with _conn() as conn:
        r = _one(conn, "SELECT * FROM qa.quarantine_record WHERE quarantine_id = :id", id=qid)
        if r is None:
            raise NotFound(f"Quarantine record {qid} does not exist.")
        history = _rows(conn, """SELECT quarantine_id, raw_id, run_id, error_code, status, created_at_utc
                                 FROM qa.quarantine_record WHERE entity = :e AND source_record_id = :s
                                 ORDER BY quarantine_id""", e=r["entity"], s=r["source_record_id"])
    r["errors"] = json.loads(r.pop("errors_json"))
    return {**r, "history": history}


def set_quarantine_status(qid: int, status: str, note: str | None) -> dict:
    """Review action: mark an open record as ignored (accepted exclusion) or reopen it."""
    allowed_from = {"ignored": "open", "open": "ignored"}[status]
    with get_engine("dw").begin() as conn:
        n = conn.execute(text("""UPDATE qa.quarantine_record SET status = :s, status_note = :n, updated_at_utc = SYSUTCDATETIME()
                                 WHERE quarantine_id = :id AND status = :f"""),
                         {"s": status, "n": (note or "")[:500] or None, "id": qid, "f": allowed_from}).rowcount
    if n == 0:
        raise NotFound(f"Quarantine record {qid} is not in status '{allowed_from}'.")
    return quarantine_detail(qid)


def list_files(page: int, page_size: int, status: str | None) -> dict:
    with _conn() as conn:
        items = _rows(conn, """SELECT file_id, source_system, file_name, file_version, status, record_count,
                                      file_size_bytes, file_checksum, duplicate_of_file_id, first_seen_run_id,
                                      processed_run_id, received_at_utc, message, message_ar
                               FROM meta.source_file WHERE (:st IS NULL OR status = :st)
                               ORDER BY file_id DESC OFFSET :o ROWS FETCH NEXT :n ROWS ONLY""",
                      st=status, o=(page - 1) * page_size, n=page_size)
        total = conn.execute(text("SELECT COUNT(*) FROM meta.source_file WHERE (:st IS NULL OR status = :st)"),
                             {"st": status}).scalar()
    return {"items": items, "page": page, "page_size": page_size, "total": total}


# --------------------------------------------------------------------------- reconciliation

def reconciliation(run_id: int | None) -> dict:
    with _conn() as conn:
        if run_id is None:
            run_id = conn.execute(text(
                "SELECT MAX(run_id) FROM meta.pipeline_run WHERE status = 'succeeded'")).scalar()
        if run_id is None:
            return {"run": None, "results": [], "entity_stats": [], "runs": []}
        run = _one(conn, "SELECT run_id, mode, status, finished_at_utc FROM meta.pipeline_run WHERE run_id = :r", r=run_id)
        if run is None:
            raise NotFound(f"Run {run_id} does not exist.")
        results = _rows(conn, """SELECT seq, area, entity, metric, source_label, warehouse_label, source_value,
                                        warehouse_value, difference, explained_difference, unexplained_difference,
                                        state, explanation, details_json
                                 FROM qa.reconciliation_result WHERE run_id = :r ORDER BY seq""", r=run_id)
        stats = _rows(conn, "SELECT * FROM meta.run_entity_stats WHERE run_id = :r ORDER BY entity", r=run_id)
        runs = _rows(conn, """SELECT TOP 30 r.run_id, r.mode, r.finished_at_utc,
                                     SUM(CASE WHEN x.state = 'unresolved' THEN 1 ELSE 0 END) AS unresolved
                              FROM meta.pipeline_run r JOIN qa.reconciliation_result x ON x.run_id = r.run_id
                              WHERE r.status = 'succeeded' GROUP BY r.run_id, r.mode, r.finished_at_utc
                              ORDER BY r.run_id DESC""")
    for r in results:
        r["details"] = json.loads(r.pop("details_json")) if r.get("details_json") else None
    return {"run": run, "results": results, "entity_stats": stats, "runs": runs}


def search_customers(q: str | None, limit: int) -> list[dict]:
    with _conn() as conn:
        return _rows(conn, """SELECT TOP (:n) c.CustomerId AS customer_id, c.CustomerCode AS customer_code,
                                     c.FullName AS full_name, c.City AS city, c.Region AS region
                              FROM dw.DimCustomer c
                              WHERE (:q IS NULL OR c.CustomerCode LIKE :like OR c.FullName LIKE :like
                                     OR CAST(c.CustomerId AS VARCHAR(12)) = :q)
                              ORDER BY c.CustomerId""",
                     n=limit, q=q or None, like=f"%{q}%" if q else None)
