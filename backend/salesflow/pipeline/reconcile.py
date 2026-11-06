"""Source-to-warehouse reconciliation (runs inside the publish transaction).

Populations are made compatible before comparing:
* Operational tables: source rows with ``updated_at <= watermark`` (the window
  this warehouse state covers). Rows modified after the window are excluded on
  both sides and reported.
* Files: the latest version of every record in accepted files (rejected and
  duplicate files are excluded by definition).

A difference is *explained* when it equals the effect of quarantined records,
*unresolved* otherwise. Orders vs collections is reported as *informational*:
unpaid orders, failed payments and refunds are legitimate business states.
"""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.engine import Connection

from ..db import get_engine
from .common import GRANT_CAP, RunContext, load_payload
from .extract import watermark_name
from .validate import TXN_STATUS_SYNONYMS, TXN_TYPE_SYNONYMS

Z = Decimal("0.00")


def _dec(v: object) -> Decimal:
    try:
        if isinstance(v, bool) or v is None:
            return Z
        return Decimal(str(v))
    except Exception:
        return Z


def _quarantined_latest(conn: Connection, entity: str) -> set[str]:
    return {r[0] for r in conn.execute(text(
        """SELECT l.source_record_id FROM (
               SELECT raw_id, source_record_id, ROW_NUMBER() OVER (PARTITION BY source_record_id ORDER BY raw_id DESC) rn
               FROM raw.source_record WHERE entity = :e) l
           JOIN qa.quarantine_record q ON q.raw_id = l.raw_id AND q.status IN ('open','ignored')
           WHERE l.rn = 1 """ + GRANT_CAP), {"e": entity})}


class _Out:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def add(self, area: str, entity: str, metric: str, src_label: str, wh_label: str, s: Decimal, w: Decimal,
            explained: Decimal, explanation: str, details: dict | None = None, informational: bool = False) -> None:
        diff = s - w
        unexplained = diff - explained
        if informational:
            state = "informational"
        elif diff == 0:
            state, unexplained, explained = "matched", Z, Z
        elif unexplained == 0:
            state = "explained"
        else:
            state = "unresolved"
        self.rows.append({"area": area, "entity": entity, "metric": metric, "source_label": src_label,
                          "warehouse_label": wh_label, "source_value": s, "warehouse_value": w, "difference": diff,
                          "explained_difference": explained, "unexplained_difference": unexplained, "state": state,
                          "explanation": explanation, "details": details})


def run_reconcile(conn: Connection, ctx: RunContext) -> dict:
    out = _Out()
    marks = {r[0]: r[1] for r in conn.execute(text("SELECT source_name, watermark_utc FROM meta.watermark"))}
    marks.update(ctx.new_watermarks)
    ops = get_engine("ops", ctx.settings)

    # ---------------------------------------------------------------- ops tables
    specs = [("customer", "customers", "customer_id", "dw.DimCustomer", "CustomerId", "Customers"),
             ("order", "orders", "order_id", "dw.DimOrder", "OrderId", "Orders"),
             ("order_item", "order_items", "order_item_id", "dw.FactSalesLine", "OrderItemId", "Order lines")]
    for entity, table, key, dw_table, dw_key, label in specs:
        bound: datetime | None = marks.get(watermark_name(table))
        with ops.connect() as oc:
            amount_cols = ", quantity, unit_price, discount_amount" if entity == "order_item" else ""
            src = {str(r[0]): r for r in oc.execute(text(
                f"SELECT {key}{amount_cols} FROM dbo.{table} WHERE updated_at <= :b"), {"b": bound})} if bound else {}
            after = {str(r[0]) for r in oc.execute(text(
                f"SELECT {key} FROM dbo.{table} WHERE :b IS NULL OR updated_at > :b"), {"b": bound})}
        if entity == "order_item":
            wh = {str(r[0]): r[1] for r in conn.execute(text(f"SELECT {dw_key}, NetAmount FROM {dw_table}"))}
        else:
            wh = {str(r[0]): None for r in conn.execute(text(f"SELECT {dw_key} FROM {dw_table}"))}
        wh_in = {k: v for k, v in wh.items() if k not in after}
        q = _quarantined_latest(conn, entity) & set(src)
        not_loaded = q - set(wh_in)
        stale = q & set(wh_in)
        expl = f"{len(not_loaded)} {label.lower()} quarantined and not loaded." if not_loaded else "All source rows loaded."
        if after:
            expl += f" {len(after)} rows modified after the extraction window are excluded from both sides."
        out.add("rows", entity, f"{label} (rows)", f"Source rows (updated_at <= {bound})", f"Rows in {dw_table}",
                Decimal(len(src)), Decimal(len(wh_in)), Decimal(len(not_loaded)), expl,
                {"quarantined_not_loaded": sorted(not_loaded)[:50], "quarantined_newer_version": sorted(stale)[:50],
                 "excluded_modified_after_window": len(after)})
        if entity == "order_item":
            def src_net(r) -> Decimal:
                return Decimal(r.quantity or 0) * _dec(r.unit_price) - _dec(r.discount_amount)
            s_amt = sum((src_net(r) for r in src.values()), Z)
            w_amt = sum((v for v in wh_in.values()), Z)
            explained = sum((src_net(src[i]) - wh_in.get(i, Z) for i in q), Z)
            out.add("amounts", entity, "Ordered value (SAR)", "Σ qty × unit price − discount in source",
                    "Σ FactSalesLine.NetAmount", s_amt, w_amt, explained,
                    f"{len(q)} quarantined lines account for {explained:.2f} SAR." if q else "No quarantined lines.")

    # ---------------------------------------------------------------- file sources
    for entity, dw_table, dw_key, label in (("product", "dw.DimProduct", "ProductSku", "Products"),
                                            ("payment_txn", "dw.FactPaymentTransaction", "TransactionId",
                                             "Payment transactions")):
        latest = {r[0]: r[1] for r in conn.execute(text(
            """SELECT source_record_id, payload_json FROM (
                   SELECT r.source_record_id, r.payload_json,
                          ROW_NUMBER() OVER (PARTITION BY r.source_record_id ORDER BY r.raw_id DESC) rn
                   FROM raw.source_record r JOIN meta.source_file f ON f.file_id = r.source_file_id
                   WHERE r.entity = :e AND f.status IN ('landed','processed')) x WHERE rn = 1 """ + GRANT_CAP),
            {"e": entity})}
        q = _quarantined_latest(conn, entity) & set(latest)
        if entity == "payment_txn":
            wh = {r[0]: (r[1], r[2], r[3]) for r in conn.execute(text(
                "SELECT TransactionId, TransactionType, TransactionStatus, Amount FROM dw.FactPaymentTransaction"))}
        else:
            wh = {r[0]: None for r in conn.execute(text(f"SELECT {dw_key} FROM {dw_table}"))}
        not_loaded = q - set(wh)
        out.add("rows", entity, f"{label} (rows)", "Latest records in accepted files", f"Rows in {dw_table}",
                Decimal(len(latest)), Decimal(len(wh)), Decimal(len(not_loaded)),
                f"{len(not_loaded)} records quarantined and not loaded." if not_loaded else "All source records loaded.",
                {"quarantined_not_loaded": sorted(not_loaded)[:50]})
        if entity == "payment_txn":
            def classify(payload: str) -> tuple[str, Decimal]:
                try:
                    p = load_payload(payload)
                except Exception:
                    return "", Z
                if not isinstance(p, dict):
                    return "", Z
                t = str(p.get("transaction_type") or "").lower()
                t = TXN_TYPE_SYNONYMS.get(t, t)
                s = str(p.get("status") or "").lower()
                s = TXN_STATUS_SYNONYMS.get(s, s)
                return (t if s == "succeeded" else ""), _dec(p.get("amount"))
            src_cls = {k: classify(v) for k, v in latest.items()}
            for ttype, metric, wh_label in (("payment", "Successful collections (SAR)", "Σ successful payments"),
                                            ("refund", "Successful refunds (SAR)", "Σ successful refunds")):
                s_amt = sum((a for c, a in src_cls.values() if c == ttype), Z)
                w_vals = {k: (v[2] if v[0] == ttype and v[1] == "succeeded" else Z) for k, v in wh.items()}
                w_amt = sum(w_vals.values(), Z)
                explained = sum(((src_cls[k][1] if src_cls[k][0] == ttype else Z) - w_vals.get(k, Z) for k in q), Z)
                out.add("amounts", entity, metric, f"Σ successful {ttype}s in accepted files", wh_label,
                        s_amt, w_amt, explained,
                        f"Quarantined transactions account for {explained:.2f} SAR." if explained else
                        "No quarantined transactions affect this total.")

    # ---------------------------------------------------------------- business view (informational)
    b = conn.execute(text(
        """WITH sales AS (
               SELECT f.OrderKey, SUM(f.NetAmount) AS ordered FROM dw.FactSalesLine f GROUP BY f.OrderKey),
           pay AS (
               SELECT p.OrderKey,
                      SUM(CASE WHEN p.TransactionType = 'payment' AND p.TransactionStatus = 'succeeded' THEN p.Amount ELSE 0 END) AS coll,
                      SUM(CASE WHEN p.TransactionType = 'refund' AND p.TransactionStatus = 'succeeded' THEN p.Amount ELSE 0 END) AS refunds
               FROM dw.FactPaymentTransaction p GROUP BY p.OrderKey)
           SELECT
               ISNULL(SUM(CASE WHEN o.IsCancelled = 0 THEN s.ordered END), 0) AS active_ordered,
               ISNULL(SUM(ISNULL(p.coll, 0) - ISNULL(p.refunds, 0)), 0) AS net_coll,
               ISNULL(SUM(CASE WHEN o.IsCancelled = 0 AND ISNULL(p.coll, 0) = 0 THEN s.ordered END), 0) AS unpaid_value,
               ISNULL(SUM(CASE WHEN o.IsCancelled = 0 AND ISNULL(p.coll, 0) = 0 THEN 1 ELSE 0 END), 0) AS unpaid_orders,
               ISNULL(SUM(CASE WHEN o.IsCancelled = 1 THEN ISNULL(p.coll, 0) - ISNULL(p.refunds, 0) END), 0) AS cancelled_net,
               ISNULL(SUM(CASE WHEN o.IsCancelled = 0 THEN ISNULL(p.refunds, 0) END), 0) AS active_refunds,
               ISNULL(SUM(CASE WHEN o.IsCancelled = 0 AND ISNULL(p.coll, 0) > 0 THEN ISNULL(p.coll, 0) - s.ordered END), 0) AS paid_vs_ordered
           FROM dw.DimOrder o
           LEFT JOIN sales s ON s.OrderKey = o.OrderKey
           LEFT JOIN pay p ON p.OrderKey = o.OrderKey """ + GRANT_CAP)).first()
    details = {
        "unpaid_active_orders": int(b.unpaid_orders),
        "unpaid_active_ordered_value": f"{b.unpaid_value:.2f}",
        "refunds_on_active_orders": f"{b.active_refunds:.2f}",
        "net_collections_on_cancelled_orders": f"{b.cancelled_net:.2f}",
        "collected_minus_ordered_on_paid_active_orders": f"{b.paid_vs_ordered:.2f}",
    }
    out.add("business", "order", "Active ordered value vs net collections (SAR)", "Active ordered value",
            "Net collections", Decimal(b.active_ordered), Decimal(b.net_coll), Z,
            f"Not an error. {b.unpaid_orders} active orders ({b.unpaid_value:.2f} SAR) have no successful payment yet "
            f"(e.g. cash on delivery, pending payment); refunds on active orders total {b.active_refunds:.2f} SAR; "
            f"cancelled orders hold {b.cancelled_net:.2f} SAR of net collections (refund not yet issued).",
            details, informational=True)

    for seq, r in enumerate(out.rows, start=1):
        conn.execute(text(
            """INSERT INTO qa.reconciliation_result (run_id, seq, area, entity, metric, source_label, warehouse_label,
                   source_value, warehouse_value, difference, explained_difference, unexplained_difference, state,
                   explanation, details_json)
               VALUES (:run, :seq, :area, :entity, :metric, :sl, :wl, :sv, :wv, :d, :ed, :ud, :st, :ex, :dj)"""),
            {"run": ctx.run_id, "seq": seq, "area": r["area"], "entity": r["entity"], "metric": r["metric"],
             "sl": r["source_label"][:200], "wl": r["warehouse_label"][:200], "sv": r["source_value"],
             "wv": r["warehouse_value"], "d": r["difference"], "ed": r["explained_difference"],
             "ud": r["unexplained_difference"], "st": r["state"], "ex": r["explanation"][:2000],
             "dj": json.dumps(r["details"]) if r["details"] else None})
    states: dict[str, int] = {}
    for r in out.rows:
        states[r["state"]] = states.get(r["state"], 0) + 1
    return {"results": len(out.rows), "states": states}
