"""Load warehouse dimensions and facts from staging (inside the publish txn).

Only staging rows touched by this run (``staged_run_id = @run``) are merged,
plus sales lines / payments of orders whose header changed. Row checksums make
the merge idempotent: identical rows are counted as unchanged, not rewritten.
Business dates are converted from UTC to Asia/Riyadh ('Arab Standard Time').
"""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.engine import Connection

from .. import geo
from ..db import bulk_json_param
from .common import GRANT_CAP, PipelineError, RunContext

log = logging.getLogger(__name__)

RIYADH_DATE = "CAST(({c} AT TIME ZONE 'UTC') AT TIME ZONE 'Arab Standard Time' AS DATE)"
DATE_KEY = "CONVERT(INT, CONVERT(CHAR(8), " + RIYADH_DATE + ", 112))"



def _chk(*cols: str) -> str:
    parts = ", ".join(f"ISNULL(CONVERT(NVARCHAR(400), {c}, 126), N'~')" for c in cols)
    return f"HASHBYTES('SHA2_256', CONCAT_WS(N'|', {parts}))"


MERGES: list[tuple[str, str, str, str]] = [
    ("customer", "dw.DimCustomer", "SELECT COUNT(*) FROM stg.customer WHERE staged_run_id = :run", f"""
MERGE dw.DimCustomer AS t
USING (SELECT customer_id, customer_code, full_name, email, city, created_at_utc, updated_at_utc,
              {_chk('customer_code', 'full_name', 'email', 'city', 'created_at_utc')} AS chk
       FROM stg.customer WHERE staged_run_id = :run) AS s
ON t.CustomerId = s.customer_id
WHEN MATCHED AND t.RowChecksum <> s.chk THEN UPDATE SET
    CustomerCode = s.customer_code, FullName = s.full_name, Email = s.email, City = s.city,
    CustomerCreatedAtUtc = s.created_at_utc, SourceUpdatedAtUtc = s.updated_at_utc, RowChecksum = s.chk,
    UpdatedRunId = :run
WHEN NOT MATCHED THEN INSERT (CustomerId, CustomerCode, FullName, Email, City, CustomerCreatedAtUtc,
    SourceUpdatedAtUtc, RowChecksum, InsertedRunId, UpdatedRunId)
    VALUES (s.customer_id, s.customer_code, s.full_name, s.email, s.city, s.created_at_utc, s.updated_at_utc,
            s.chk, :run, :run)
OUTPUT $action INTO @out
{GRANT_CAP};"""),
    ("product", "dw.DimProduct", "SELECT COUNT(*) FROM stg.product WHERE staged_run_id = :run", f"""
MERGE dw.DimProduct AS t
USING (SELECT product_sku, product_name, category, list_price, is_active, source_file_version,
              {_chk('product_name', 'category', 'list_price', 'is_active')} AS chk
       FROM stg.product WHERE staged_run_id = :run) AS s
ON t.ProductSku = s.product_sku
WHEN MATCHED AND t.RowChecksum <> s.chk THEN UPDATE SET
    ProductName = s.product_name, Category = s.category, CurrentListPrice = s.list_price, IsActive = s.is_active,
    CatalogVersion = s.source_file_version, RowChecksum = s.chk, UpdatedRunId = :run
WHEN NOT MATCHED THEN INSERT (ProductSku, ProductName, Category, CurrentListPrice, IsActive, CatalogVersion,
    RowChecksum, InsertedRunId, UpdatedRunId)
    VALUES (s.product_sku, s.product_name, s.category, s.list_price, s.is_active, s.source_file_version,
            s.chk, :run, :run)
OUTPUT $action INTO @out
{GRANT_CAP};"""),
    ("order", "dw.DimOrder", "SELECT COUNT(*) FROM stg.sales_order WHERE staged_run_id = :run", f"""
MERGE dw.DimOrder AS t
USING (SELECT o.order_id, c.CustomerKey, o.order_placed_at_utc, o.order_status, o.channel, o.updated_at_utc,
              {DATE_KEY.format(c='o.order_placed_at_utc')} AS date_key,
              {_chk('c.CustomerKey', 'o.order_placed_at_utc', 'o.order_status', 'o.channel')} AS chk
       FROM stg.sales_order o JOIN dw.DimCustomer c ON c.CustomerId = o.customer_id
       WHERE o.staged_run_id = :run) AS s
ON t.OrderId = s.order_id
WHEN MATCHED AND t.RowChecksum <> s.chk THEN UPDATE SET
    CustomerKey = s.CustomerKey, OrderPlacedAtUtc = s.order_placed_at_utc, OrderDateKey = s.date_key,
    OrderStatus = s.order_status, Channel = s.channel, SourceUpdatedAtUtc = s.updated_at_utc,
    RowChecksum = s.chk, UpdatedRunId = :run
WHEN NOT MATCHED THEN INSERT (OrderId, CustomerKey, OrderPlacedAtUtc, OrderDateKey, OrderStatus, Channel,
    SourceUpdatedAtUtc, RowChecksum, InsertedRunId, UpdatedRunId)
    VALUES (s.order_id, s.CustomerKey, s.order_placed_at_utc, s.date_key, s.order_status, s.channel,
            s.updated_at_utc, s.chk, :run, :run)
OUTPUT $action INTO @out
{GRANT_CAP};"""),
    ("order_item", "dw.FactSalesLine",
     """SELECT COUNT(*) FROM stg.order_item i WHERE i.staged_run_id = :run
        OR i.order_id IN (SELECT order_id FROM stg.sales_order WHERE staged_run_id = :run)""", f"""
MERGE dw.FactSalesLine AS t
USING (SELECT i.order_item_id, o.OrderKey, o.OrderDateKey, o.CustomerKey, p.ProductKey, i.line_number,
              i.quantity, i.unit_price, i.discount_amount,
              {_chk('o.OrderKey', 'o.OrderDateKey', 'o.CustomerKey', 'p.ProductKey', 'i.line_number',
                    'i.quantity', 'i.unit_price', 'i.discount_amount')} AS chk
       FROM stg.order_item i
       JOIN dw.DimOrder o ON o.OrderId = i.order_id
       JOIN dw.DimProduct p ON p.ProductSku = i.product_sku
       WHERE i.staged_run_id = :run
          OR i.order_id IN (SELECT order_id FROM stg.sales_order WHERE staged_run_id = :run)) AS s
ON t.OrderItemId = s.order_item_id
WHEN MATCHED AND t.RowChecksum <> s.chk THEN UPDATE SET
    OrderKey = s.OrderKey, OrderDateKey = s.OrderDateKey, CustomerKey = s.CustomerKey, ProductKey = s.ProductKey,
    LineNumber = s.line_number, Quantity = s.quantity, UnitPrice = s.unit_price, DiscountAmount = s.discount_amount,
    RowChecksum = s.chk, UpdatedRunId = :run
WHEN NOT MATCHED THEN INSERT (OrderItemId, OrderKey, OrderDateKey, CustomerKey, ProductKey, LineNumber, Quantity,
    UnitPrice, DiscountAmount, RowChecksum, InsertedRunId, UpdatedRunId)
    VALUES (s.order_item_id, s.OrderKey, s.OrderDateKey, s.CustomerKey, s.ProductKey, s.line_number, s.quantity,
            s.unit_price, s.discount_amount, s.chk, :run, :run)
OUTPUT $action INTO @out
{GRANT_CAP};"""),
    ("payment_txn", "dw.FactPaymentTransaction",
     """SELECT COUNT(*) FROM stg.payment_txn x WHERE x.staged_run_id = :run
        OR x.order_id IN (SELECT order_id FROM stg.sales_order WHERE staged_run_id = :run)""", f"""
MERGE dw.FactPaymentTransaction AS t
USING (SELECT x.transaction_id, o.OrderKey, o.CustomerKey, x.transaction_at_utc, x.transaction_type,
              x.transaction_status, x.amount, x.payment_method, x.refers_to_transaction_id, x.source_file_id,
              {DATE_KEY.format(c='x.transaction_at_utc')} AS date_key,
              {_chk('o.OrderKey', 'o.CustomerKey', 'x.transaction_at_utc', 'x.transaction_type',
                    'x.transaction_status', 'x.amount', 'x.payment_method', 'x.refers_to_transaction_id')} AS chk
       FROM stg.payment_txn x JOIN dw.DimOrder o ON o.OrderId = x.order_id
       WHERE x.staged_run_id = :run
          OR x.order_id IN (SELECT order_id FROM stg.sales_order WHERE staged_run_id = :run)) AS s
ON t.TransactionId = s.transaction_id
WHEN MATCHED AND t.RowChecksum <> s.chk THEN UPDATE SET
    OrderKey = s.OrderKey, CustomerKey = s.CustomerKey, TransactionDateKey = s.date_key,
    TransactionAtUtc = s.transaction_at_utc, TransactionType = s.transaction_type,
    TransactionStatus = s.transaction_status, Amount = s.amount, PaymentMethod = s.payment_method,
    RefersToTransactionId = s.refers_to_transaction_id, SourceFileId = s.source_file_id,
    RowChecksum = s.chk, UpdatedRunId = :run
WHEN NOT MATCHED THEN INSERT (TransactionId, OrderKey, CustomerKey, TransactionDateKey, TransactionAtUtc,
    TransactionType, TransactionStatus, Amount, PaymentMethod, RefersToTransactionId, SourceFileId, RowChecksum,
    InsertedRunId, UpdatedRunId)
    VALUES (s.transaction_id, s.OrderKey, s.CustomerKey, s.date_key, s.transaction_at_utc, s.transaction_type,
            s.transaction_status, s.amount, s.payment_method, s.refers_to_transaction_id, s.source_file_id, s.chk,
            :run, :run)
OUTPUT $action INTO @out
{GRANT_CAP};"""),
]

# Every staged row must find its parents; an inner join would otherwise drop it silently.
ORPHAN_CHECKS = {
    "order": "SELECT COUNT(*) FROM stg.sales_order o LEFT JOIN dw.DimCustomer c ON c.CustomerId = o.customer_id "
             "WHERE o.staged_run_id = :run AND c.CustomerKey IS NULL",
    "order_item": "SELECT COUNT(*) FROM stg.order_item i LEFT JOIN dw.DimOrder o ON o.OrderId = i.order_id "
                  "LEFT JOIN dw.DimProduct p ON p.ProductSku = i.product_sku "
                  "WHERE i.staged_run_id = :run AND (o.OrderKey IS NULL OR p.ProductKey IS NULL)",
    "payment_txn": "SELECT COUNT(*) FROM stg.payment_txn x LEFT JOIN dw.DimOrder o ON o.OrderId = x.order_id "
                   "WHERE x.staged_run_id = :run AND o.OrderKey IS NULL",
}


def run_load(conn: Connection, ctx: RunContext) -> dict:
    details = {}
    for entity, table, count_sql, merge_sql in MERGES:
        if entity in ORPHAN_CHECKS:
            orphans = conn.execute(text(ORPHAN_CHECKS[entity]), {"run": ctx.run_id}).scalar()
            if orphans:
                raise PipelineError("INTEGRITY_ERROR",
                                    f"{orphans} staged {entity} rows have no parent row in the warehouse.")
        source_rows = conn.execute(text(count_sql), {"run": ctx.run_id}).scalar()
        res = conn.execute(text(
            "SET NOCOUNT ON; DECLARE @out TABLE (act NVARCHAR(10));" + merge_sql +
            " SELECT ISNULL(SUM(CASE WHEN act = 'INSERT' THEN 1 ELSE 0 END), 0) AS ins,"
            " ISNULL(SUM(CASE WHEN act = 'UPDATE' THEN 1 ELSE 0 END), 0) AS upd FROM @out;"),
            {"run": ctx.run_id}).first()
        ins, upd = int(res[0]), int(res[1])
        st = ctx.stats[entity]
        st.rows_inserted += ins
        st.rows_updated += upd
        st.rows_dw_unchanged += source_rows - ins - upd
        details[table] = {"candidates": source_rows, "inserted": ins, "updated": upd,
                          "unchanged": source_rows - ins - upd}
        if entity == "customer":
            _refresh_regions(conn, ctx)
    return details


def _refresh_regions(conn: Connection, ctx: RunContext) -> None:
    """Derive DimCustomer.Region from City for every customer whose region is missing or out of date.

    Runs over all customers, not only this run's, so it also back-fills rows loaded before the
    column existed and follows any change to the geography reference."""
    n = conn.execute(text(
        """SET NOCOUNT ON;
           UPDATE c SET Region = g.region, UpdatedRunId = :run
           FROM dw.DimCustomer c
           LEFT JOIN OPENJSON(:geo) WITH (city NVARCHAR(100), region NVARCHAR(60)) g ON g.city = c.City
           WHERE ISNULL(c.Region, N'') <> ISNULL(g.region, N'');
           SELECT @@ROWCOUNT;"""),
        {"run": ctx.run_id, "geo": bulk_json_param(geo.city_region_pairs())}).scalar()
    if n:
        log.info("customer regions refreshed", extra={"rows": n})
