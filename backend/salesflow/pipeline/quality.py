"""Quality checks: record-level results from staging, file-level results from
ingestion, and a warehouse quality gate. A failed *error* check aborts the run
before publish, so a broken load never becomes the visible warehouse state."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Connection

from .common import PipelineError, RunContext
from .validate import CHECKS

WAREHOUSE_CHECKS: list[tuple[str, str, str, str, str, str]] = [
    # code, name, entity, severity, checked_sql, failed_sql
    ("WH_STG_CUSTOMER_COUNT", "Every accepted customer is in DimCustomer", "customer", "error",
     "SELECT COUNT(*) FROM stg.customer",
     "SELECT COUNT(*) FROM stg.customer s LEFT JOIN dw.DimCustomer d ON d.CustomerId = s.customer_id WHERE d.CustomerKey IS NULL"),
    ("WH_STG_PRODUCT_COUNT", "Every accepted product is in DimProduct", "product", "error",
     "SELECT COUNT(*) FROM stg.product",
     "SELECT COUNT(*) FROM stg.product s LEFT JOIN dw.DimProduct d ON d.ProductSku = s.product_sku WHERE d.ProductKey IS NULL"),
    ("WH_STG_ORDER_COUNT", "Every accepted order is in DimOrder", "order", "error",
     "SELECT COUNT(*) FROM stg.sales_order",
     "SELECT COUNT(*) FROM stg.sales_order s LEFT JOIN dw.DimOrder d ON d.OrderId = s.order_id WHERE d.OrderKey IS NULL"),
    ("WH_STG_LINE_COUNT", "Every accepted order line is in FactSalesLine", "order_item", "error",
     "SELECT COUNT(*) FROM stg.order_item",
     "SELECT COUNT(*) FROM stg.order_item s LEFT JOIN dw.FactSalesLine f ON f.OrderItemId = s.order_item_id WHERE f.SalesLineKey IS NULL"),
    ("WH_STG_TXN_COUNT", "Every accepted transaction is in FactPaymentTransaction", "payment_txn", "error",
     "SELECT COUNT(*) FROM stg.payment_txn",
     "SELECT COUNT(*) FROM stg.payment_txn s LEFT JOIN dw.FactPaymentTransaction f ON f.TransactionId = s.transaction_id WHERE f.PaymentTxnKey IS NULL"),
    ("WH_LINE_VALUES_MATCH_STG", "Sales line quantity, price and discount match staging", "order_item", "error",
     "SELECT COUNT(*) FROM dw.FactSalesLine",
     """SELECT COUNT(*) FROM dw.FactSalesLine f JOIN stg.order_item s ON s.order_item_id = f.OrderItemId
        WHERE f.Quantity <> s.quantity OR f.UnitPrice <> s.unit_price OR f.DiscountAmount <> s.discount_amount"""),
    ("WH_LINE_DATE_MATCHES_ORDER", "Sales line order date equals its order header date", "order_item", "error",
     "SELECT COUNT(*) FROM dw.FactSalesLine",
     "SELECT COUNT(*) FROM dw.FactSalesLine f JOIN dw.DimOrder o ON o.OrderKey = f.OrderKey WHERE f.OrderDateKey <> o.OrderDateKey OR f.CustomerKey <> o.CustomerKey"),
    ("WH_PAYMENT_AFTER_ORDER", "No transaction is dated before its order", "payment_txn", "error",
     "SELECT COUNT(*) FROM dw.FactPaymentTransaction",
     "SELECT COUNT(*) FROM dw.FactPaymentTransaction f JOIN dw.DimOrder o ON o.OrderKey = f.OrderKey WHERE f.TransactionAtUtc < o.OrderPlacedAtUtc"),
    ("WH_REFUNDS_WITHIN_COLLECTIONS", "Successful refunds per order do not exceed successful payments", "payment_txn", "error",
     "SELECT COUNT(DISTINCT OrderKey) FROM dw.FactPaymentTransaction",
     """SELECT COUNT(*) FROM (SELECT OrderKey FROM dw.FactPaymentTransaction GROUP BY OrderKey
        HAVING SUM(NetCollectionAmount) < 0) x"""),
    ("WH_ORDERS_WITHOUT_LINES", "Orders have at least one accepted sales line", "order", "warning",
     "SELECT COUNT(*) FROM dw.DimOrder",
     "SELECT COUNT(*) FROM dw.DimOrder o WHERE NOT EXISTS (SELECT 1 FROM dw.FactSalesLine f WHERE f.OrderKey = o.OrderKey)"),
    ("WH_ORDERS_WITH_QUARANTINED_LINES", "Orders have no quarantined lines", "order", "warning",
     "SELECT COUNT(*) FROM dw.DimOrder",
     """SELECT COUNT(DISTINCT o.OrderKey) FROM dw.DimOrder o
        JOIN qa.quarantine_record q ON q.entity = 'order_item' AND q.status = 'open'
         AND TRY_CAST(CASE WHEN ISJSON(q.payload) = 1 THEN JSON_VALUE(q.payload, '$.order_id') END AS INT) = o.OrderId"""),
]

# File problems are handled (rejected / skipped / held), so they warn rather than fail the run.
FILE_CHECKS = [
    ("FILE_SCHEMA_VALID", "Delivered files match their schema contract", "rejected"),
    ("FILE_NOT_DUPLICATE", "Files are not duplicate deliveries", "duplicate"),
    ("FILE_NAME_UNIQUE", "Files do not reuse an accepted file name with new content", "conflict"),
]


def _insert(conn: Connection, ctx: RunContext, code: str, name: str, level: str, entity: str, severity: str,
            checked: int, failed: int, details: str | None = None) -> None:
    conn.execute(text(
        """INSERT INTO qa.check_result (run_id, check_code, check_name, check_level, entity, severity, status,
                                        checked_count, failed_count, details)
           VALUES (:run, :c, :n, :l, :e, :s, :st, :cc, :fc, :d)"""),
        {"run": ctx.run_id, "c": code, "n": name, "l": level, "e": entity, "s": severity,
         "st": "failed" if failed else "passed", "cc": checked, "fc": failed, "d": details})


def run_quality(conn: Connection, ctx: RunContext) -> dict:
    # Record-level: one row per applicable rule and entity evaluated this run.
    rec_failed = 0
    for code, (name, entities) in CHECKS.items():
        for entity in entities:
            checked = ctx.evaluated.get(entity, 0)
            if not checked:
                continue
            failed = ctx.record_failures.get((entity, code), 0)
            rec_failed += failed
            # Record-level failures are quarantined, not fatal: severity 'warning' for the gate.
            _insert(conn, ctx, code, name, "record", entity, "warning", checked, failed)
    # File-level.
    files = ctx.file_events
    for code, name, status in FILE_CHECKS if files else ():
        bad = [e["file"] for e in files if e["status"] == status]
        _insert(conn, ctx, code, name, "file", "file", "warning", len(files), len(bad), ", ".join(bad)[:2000] or None)
    # Warehouse gate.
    gate_failures = []
    for code, name, entity, severity, checked_sql, failed_sql in WAREHOUSE_CHECKS:
        checked = conn.execute(text(checked_sql)).scalar() or 0
        failed = conn.execute(text(failed_sql)).scalar() or 0
        _insert(conn, ctx, code, name, "warehouse", entity, severity, checked, failed)
        ctx.warehouse_checks.append({"code": code, "failed": failed, "severity": severity})
        if failed and severity == "error":
            gate_failures.append(f"{code} ({failed})")
    if gate_failures:
        raise PipelineError("QUALITY_GATE_FAILED",
                            "Warehouse quality gate failed: " + ", ".join(gate_failures) + ". Nothing was published.")
    return {"record_failures": rec_failed, "warehouse_checks": len(WAREHOUSE_CHECKS), "gate": "passed"}
