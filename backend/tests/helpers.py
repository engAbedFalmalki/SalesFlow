"""Independent SQL oracles and manifest arithmetic for tests."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import text

from salesflow.db import get_engine
from salesflow.generator.reference import ADDITIVE_INT, ADDITIVE_MONEY

MONEY_KEYS = ADDITIVE_MONEY


def warehouse_totals() -> dict:
    """Totals computed with SQL written independently of salesflow.analytics."""
    with get_engine("dw").connect() as c:
        r = c.execute(text("""
            SELECT
              (SELECT COUNT(*) FROM dw.DimCustomer) AS customers,
              (SELECT COUNT(*) FROM dw.DimProduct) AS products,
              (SELECT COUNT(*) FROM dw.DimOrder) AS orders,
              (SELECT COUNT(*) FROM dw.FactSalesLine) AS sales_lines,
              (SELECT COUNT(*) FROM dw.FactPaymentTransaction) AS payment_transactions,
              (SELECT ISNULL(SUM(CAST(Quantity AS DECIMAL(18,2)) * UnitPrice), 0) FROM dw.FactSalesLine) AS gmv,
              (SELECT ISNULL(SUM(CAST(Quantity AS DECIMAL(18,2)) * UnitPrice - DiscountAmount), 0) FROM dw.FactSalesLine) AS ordered,
              (SELECT ISNULL(SUM(CAST(Quantity AS DECIMAL(18,2)) * UnitPrice - DiscountAmount), 0) FROM dw.FactSalesLine
                 WHERE OrderKey IN (SELECT OrderKey FROM dw.DimOrder WHERE OrderStatus <> 'cancelled')) AS active,
              (SELECT COUNT(*) FROM dw.DimOrder o WHERE o.OrderStatus <> 'cancelled'
                 AND EXISTS (SELECT 1 FROM dw.FactSalesLine f WHERE f.OrderKey = o.OrderKey)) AS active_orders,
              (SELECT ISNULL(SUM(Amount), 0) FROM dw.FactPaymentTransaction
                 WHERE TransactionType = 'payment' AND TransactionStatus = 'succeeded') AS coll,
              (SELECT ISNULL(SUM(Amount), 0) FROM dw.FactPaymentTransaction
                 WHERE TransactionType = 'refund' AND TransactionStatus = 'succeeded') AS refunds""")).first()
        statuses = {s: n for s, n in c.execute(text("SELECT OrderStatus, COUNT(*) FROM dw.DimOrder GROUP BY OrderStatus"))}
    return {
        "customers": r.customers, "products": r.products, "orders": r.orders, "sales_lines": r.sales_lines,
        "payment_transactions": r.payment_transactions, "distinct_active_orders": r.active_orders,
        "gross_merchandise_value": f"{r.gmv:.2f}", "ordered_value": f"{r.ordered:.2f}",
        "active_ordered_value": f"{r.active:.2f}", "successful_collections": f"{r.coll:.2f}",
        "successful_refunds": f"{r.refunds:.2f}", "net_collections": f"{r.coll - r.refunds:.2f}",
        "order_status_counts": dict(sorted(statuses.items())),
    }


def scenario(manifest: dict, sid: str) -> dict:
    return next(s for s in manifest["scenarios"] if s["id"] == sid)


def expected_totals(manifest: dict, *scenario_ids: str) -> dict:
    """Baseline totals plus the deltas of the given scenarios."""
    t = dict(scenario(manifest, "s01_initial_load")["expected"]["warehouse_totals"])
    t["order_status_counts"] = dict(t["order_status_counts"])
    for sid in scenario_ids:
        d = scenario(manifest, sid)["expected"]["warehouse_delta"]
        for k in ADDITIVE_INT:
            t[k] += d[k]
        for k in ADDITIVE_MONEY:
            t[k] = f"{Decimal(t[k]) + Decimal(d[k]):.2f}"
        for s, n in d["order_status_counts"].items():
            t["order_status_counts"][s] = t["order_status_counts"].get(s, 0) + n
    t["order_status_counts"] = {k: v for k, v in sorted(t["order_status_counts"].items()) if v}
    return t


def open_quarantine_by_code(entity: str | None = None) -> dict[str, int]:
    with get_engine("dw").connect() as c:
        rows = c.execute(text("""SELECT error_code, COUNT(*) FROM qa.quarantine_record WHERE status = 'open'
                                 AND (:e IS NULL OR entity = :e) GROUP BY error_code"""), {"e": entity})
        return {r[0]: r[1] for r in rows}


def scalar(sql: str, **p):
    with get_engine("dw").connect() as c:
        return c.execute(text(sql), p).scalar()


def rows(sql: str, **p) -> list[dict]:
    with get_engine("dw").connect() as c:
        return [dict(r._mapping) for r in c.execute(text(sql), p)]


def unresolved_reconciliation(run_id: int | None = None) -> list[dict]:
    rid = run_id or scalar("SELECT MAX(run_id) FROM meta.pipeline_run WHERE status = 'succeeded'")
    return rows("SELECT metric, difference, explanation FROM qa.reconciliation_result WHERE run_id = :r AND state = 'unresolved'", r=rid)
