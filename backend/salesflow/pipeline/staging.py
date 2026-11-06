"""Staging: turn pending raw versions into accepted typed rows or quarantine.

Which raw rows are evaluated ("pending") is derived from state, not from the
run that landed them: the latest raw version of a record is pending when it
differs from the accepted staging row and is not already quarantined. That is
what makes retries after a failure safe (nothing is "lost" in a failed run)
and reprocessing explicit (open quarantine records are only re-evaluated by a
``reprocess`` run).

Runs inside the publish transaction: if anything later fails, staging and
quarantine changes roll back together with the warehouse.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from typing import Callable

from sqlalchemy import text
from sqlalchemy.engine import Connection

from ..db import bulk_json_param, chunked
from .common import RunContext, load_payload
from .validate import Issue, parse_customer, parse_order, parse_order_item, parse_payment, parse_product

ORDER = ("customer", "product", "order", "order_item", "payment_txn")
SOURCE_SYSTEM = {"customer": "ops_sql", "order": "ops_sql", "order_item": "ops_sql",
                 "product": "product_catalog_csv", "payment_txn": "payments_json"}

STG: dict[str, dict] = {
    "customer": {"table": "stg.customer", "key": "customer_id", "parse": parse_customer, "cols": [
        ("customer_id", "INT"), ("customer_code", "NVARCHAR(20)"), ("first_name", "NVARCHAR(100)"),
        ("last_name", "NVARCHAR(100)"), ("full_name", "NVARCHAR(201)"), ("email", "NVARCHAR(200)"),
        ("city", "NVARCHAR(100)"), ("created_at_utc", "DATETIME2(3)"), ("updated_at_utc", "DATETIME2(3)")]},
    "product": {"table": "stg.product", "key": "product_sku", "parse": parse_product, "cols": [
        ("product_sku", "NVARCHAR(40)"), ("product_name", "NVARCHAR(200)"), ("category", "NVARCHAR(100)"),
        ("list_price", "DECIMAL(18,2)"), ("currency", "CHAR(3)"), ("is_active", "BIT"),
        ("source_updated_at_utc", "DATETIME2(3)"), ("source_file_id", "INT"), ("source_file_version", "INT")]},
    "order": {"table": "stg.sales_order", "key": "order_id", "parse": parse_order, "cols": [
        ("order_id", "INT"), ("customer_id", "INT"), ("order_placed_at_utc", "DATETIME2(3)"),
        ("order_status", "NVARCHAR(20)"), ("currency", "CHAR(3)"), ("channel", "NVARCHAR(20)"),
        ("created_at_utc", "DATETIME2(3)"), ("updated_at_utc", "DATETIME2(3)")]},
    "order_item": {"table": "stg.order_item", "key": "order_item_id", "parse": parse_order_item, "cols": [
        ("order_item_id", "INT"), ("order_id", "INT"), ("line_number", "INT"), ("product_sku", "NVARCHAR(40)"),
        ("quantity", "INT"), ("unit_price", "DECIMAL(18,2)"), ("discount_amount", "DECIMAL(18,2)"),
        ("created_at_utc", "DATETIME2(3)"), ("updated_at_utc", "DATETIME2(3)")]},
    "payment_txn": {"table": "stg.payment_txn", "key": "transaction_id", "parse": parse_payment, "cols": [
        ("transaction_id", "NVARCHAR(40)"), ("order_id", "INT"), ("transaction_type", "NVARCHAR(10)"),
        ("transaction_status", "NVARCHAR(12)"), ("amount", "DECIMAL(18,2)"), ("currency", "CHAR(3)"),
        ("payment_method", "NVARCHAR(30)"), ("transaction_at_utc", "DATETIME2(3)"),
        ("refers_to_transaction_id", "NVARCHAR(40)"), ("source_file_id", "INT")]},
}
_META_COLS = [("raw_id", "BIGINT"), ("source_checksum", "CHAR(64)")]


class RefState:
    """Accepted reference data, updated as records are accepted in this run."""

    def __init__(self, conn: Connection):
        self.customers = {r[0] for r in conn.execute(text("SELECT customer_id FROM stg.customer"))}
        self.products = {r[0]: r[1] for r in conn.execute(text("SELECT product_sku, source_file_version FROM stg.product"))}
        self.orders = {r[0]: r[1] for r in conn.execute(text("SELECT order_id, order_placed_at_utc FROM stg.sales_order"))}
        self.payments: dict[str, dict] = {}
        self.refunds: dict[str, dict[str, Decimal]] = defaultdict(dict)  # payment id -> {refund id: amount}
        for r in conn.execute(text("""SELECT transaction_id, order_id, transaction_type, transaction_status, amount,
                                             transaction_at_utc, refers_to_transaction_id FROM stg.payment_txn""")):
            self._add_payment(dict(r._mapping))

    def _add_payment(self, t: dict) -> None:
        old = self.payments.get(t["transaction_id"])
        if old and old.get("refers_to_transaction_id"):
            self.refunds[old["refers_to_transaction_id"]].pop(t["transaction_id"], None)
        self.payments[t["transaction_id"]] = t
        if t["transaction_type"] == "refund" and t["transaction_status"] == "succeeded" and t["refers_to_transaction_id"]:
            self.refunds[t["refers_to_transaction_id"]][t["transaction_id"]] = t["amount"]

    def check(self, entity: str, rec: dict, issues: list[Issue]) -> None:
        if entity == "order" and rec["customer_id"] not in self.customers:
            issues.append(Issue("MISSING_CUSTOMER_REF", "customer_id",
                                f"Customer {rec['customer_id']} is not an accepted customer (unknown or quarantined).",
                                f"العميل {rec['customer_id']} ليس عميلاً مقبولاً (غير معروف أو في الحجر)."))
        elif entity == "order_item":
            if rec["order_id"] not in self.orders:
                issues.append(Issue("MISSING_ORDER_REF", "order_id",
                                    f"Order {rec['order_id']} is not an accepted order (unknown or quarantined).",
                                    f"الطلب {rec['order_id']} ليس طلباً مقبولاً (غير معروف أو في الحجر)."))
            if rec["product_sku"] not in self.products:
                issues.append(Issue("MISSING_PRODUCT_REF", "product_sku",
                                    f"Product {rec['product_sku']} is not in the accepted catalog.",
                                    f"المنتج {rec['product_sku']} غير موجود في الكتالوج المقبول."))
        elif entity == "payment_txn":
            placed = self.orders.get(rec["order_id"])
            if placed is None:
                issues.append(Issue("MISSING_ORDER_REF", "order_id",
                                    f"Order {rec['order_id']} is not an accepted order (unknown or quarantined).",
                                    f"الطلب {rec['order_id']} ليس طلباً مقبولاً (غير معروف أو في الحجر)."))
            elif rec["transaction_at_utc"] < placed:
                issues.append(Issue("IMPOSSIBLE_TIMESTAMP", "transaction_at",
                                    f"Transaction at {rec['transaction_at_utc']} is before the order was placed ({placed}).",
                                    f"وقت العملية {rec['transaction_at_utc']} أسبق من وقت إنشاء الطلب ({placed})."))
            if rec["transaction_type"] == "refund":
                ref = self.payments.get(rec["refers_to_transaction_id"])
                if (ref is None or ref["transaction_type"] != "payment" or ref["transaction_status"] != "succeeded"
                        or ref["order_id"] != rec["order_id"]):
                    issues.append(Issue("INVALID_REFUND_REF", "refers_to_transaction_id",
                                        f"Refund references '{rec['refers_to_transaction_id']}', which is not a "
                                        f"successful payment of order {rec['order_id']}.",
                                        f"الاسترداد يشير إلى '{rec['refers_to_transaction_id']}'، وهي ليست دفعة ناجحة للطلب {rec['order_id']}."))
                else:
                    if rec["transaction_at_utc"] < ref["transaction_at_utc"]:
                        issues.append(Issue("IMPOSSIBLE_TIMESTAMP", "transaction_at",
                                            "Refund is dated before the payment it refunds.", "تاريخ الاسترداد أسبق من تاريخ الدفعة التي يستردها."))
                    if rec["transaction_status"] == "succeeded":
                        prior = sum((a for tid, a in self.refunds[ref["transaction_id"]].items()
                                     if tid != rec["transaction_id"]), Decimal("0.00"))
                        if prior + rec["amount"] > ref["amount"]:
                            issues.append(Issue("REFUND_EXCEEDS_COLLECTION", "amount",
                                                f"Successful refunds ({prior + rec['amount']:.2f}) would exceed the "
                                                f"refunded payment {ref['transaction_id']} ({ref['amount']:.2f}).",
                                                f"مجموع الاستردادات الناجحة ({prior + rec['amount']:.2f}) سيتجاوز الدفعة "
                                                f"المستردة {ref['transaction_id']} ({ref['amount']:.2f})."))

    def accept(self, entity: str, rec: dict) -> None:
        if entity == "customer":
            self.customers.add(rec["customer_id"])
        elif entity == "product":
            self.products[rec["product_sku"]] = rec["source_file_version"]
        elif entity == "order":
            self.orders[rec["order_id"]] = rec["order_placed_at_utc"]
        elif entity == "payment_txn":
            self._add_payment(rec)


def _latest(conn: Connection, entity: str) -> list[dict]:
    return [dict(r._mapping) for r in conn.execute(text(
        """SELECT raw_id, source_record_id, payload_checksum, source_file_id, source_file_name, run_id FROM (
               SELECT raw_id, source_record_id, payload_checksum, source_file_id, source_file_name, run_id,
                      ROW_NUMBER() OVER (PARTITION BY source_record_id ORDER BY raw_id DESC) AS rn
               FROM raw.source_record WHERE entity = :e) x WHERE rn = 1"""), {"e": entity})]


def _select_candidates(conn: Connection, ctx: RunContext, entity: str) -> list[dict]:
    latest = _latest(conn, entity)
    if ctx.mode == "reprocess" and ctx.params["scope"] == "quarantine":
        if ctx.params.get("entity") and ctx.params["entity"] != entity:
            return []
        codes = ctx.params.get("error_codes") or []
        sql = "SELECT raw_id FROM qa.quarantine_record WHERE entity = :e AND status = 'open' AND raw_id IS NOT NULL"
        rows = conn.execute(text(sql + (" AND error_code IN (SELECT value FROM OPENJSON(:c))" if codes else "")),
                            {"e": entity, "c": json.dumps(codes)})
        targets = {r[0] for r in rows}
        return [l for l in latest if l["raw_id"] in targets]
    if ctx.mode == "reprocess" and ctx.params["scope"] == "run":
        return [l for l in latest if l["run_id"] == ctx.params["source_run_id"]]
    cfg = STG[entity]
    staged = {str(r[0]): r[1] for r in conn.execute(text(f"SELECT {cfg['key']}, source_checksum FROM {cfg['table']}"))}
    quarantined = {r[0] for r in conn.execute(text(
        "SELECT raw_id FROM qa.quarantine_record WHERE entity = :e AND status IN ('open','ignored') AND raw_id IS NOT NULL"),
        {"e": entity})}
    return [l for l in latest
            if staged.get(l["source_record_id"]) != l["payload_checksum"] and l["raw_id"] not in quarantined]


def _payloads(conn: Connection, raw_ids: list[int]) -> dict[int, str]:
    out: dict[int, str] = {}
    for chunk in chunked(raw_ids, 5000):
        for r in conn.execute(text("SELECT raw_id, payload_json FROM raw.source_record "
                                   "WHERE raw_id IN (SELECT CAST(value AS BIGINT) FROM OPENJSON(:j))"),
                              {"j": json.dumps(chunk)}):
            out[r[0]] = r[1]
    return out


def _merge_stg(conn: Connection, ctx: RunContext, entity: str, rows: list[dict]) -> None:
    cfg = STG[entity]
    cols = cfg["cols"] + _META_COLS
    key = cfg["key"]
    names = [c for c, _ in cols]
    with_clause = ", ".join(f"{c} {t}" for c, t in cols)
    sets = ", ".join(f"t.{c} = s.{c}" for c in names if c != key)
    sql = (f"MERGE {cfg['table']} AS t USING (SELECT * FROM OPENJSON(:j) WITH ({with_clause})) AS s "
           f"ON t.{key} = s.{key} "
           f"WHEN MATCHED THEN UPDATE SET {sets}, t.staged_run_id = :run "
           f"WHEN NOT MATCHED THEN INSERT ({', '.join(names)}, staged_run_id) "
           f"VALUES ({', '.join('s.' + c for c in names)}, :run);")
    for chunk in chunked(rows, 4000):
        conn.execute(text(sql), {"j": bulk_json_param(_ser(r, names) for r in chunk), "run": ctx.run_id})


def _ser(rec: dict, names: list[str]) -> dict:
    out = {}
    for n in names:
        v = rec.get(n)
        if isinstance(v, datetime):
            v = v.isoformat(timespec="milliseconds")
        elif isinstance(v, Decimal):
            v = f"{v:.2f}"
        elif isinstance(v, bool):
            v = 1 if v else 0
        out[n] = v
    return out


def _write_quarantine(conn: Connection, ctx: RunContext, entity: str, rejected: list[dict], accepted: list[dict]) -> None:
    if rejected:
        for chunk in chunked(rejected, 2000):
            j = bulk_json_param(chunk)
            # Re-evaluated records (reprocess) update their existing open row.
            conn.execute(text(
                """UPDATE q SET evaluation_count = q.evaluation_count + 1, last_evaluated_run_id = :run,
                          error_code = s.error_code, error_reason = s.error_reason,
                          error_reason_ar = s.error_reason_ar, errors_json = s.errors_json,
                          updated_at_utc = SYSUTCDATETIME()
                   FROM qa.quarantine_record q
                   JOIN OPENJSON(:j) WITH (raw_id BIGINT, error_code NVARCHAR(50), error_reason NVARCHAR(1000),
                                           error_reason_ar NVARCHAR(1000), errors_json NVARCHAR(MAX)) s
                     ON s.raw_id = q.raw_id
                   WHERE q.entity = :e AND q.status IN ('open','ignored')"""), {"j": j, "run": ctx.run_id, "e": entity})
            conn.execute(text(
                """INSERT INTO qa.quarantine_record (entity, source_system, source_record_id, raw_id, source_file_id,
                       source_file_name, run_id, error_code, error_reason, error_reason_ar, errors_json, payload,
                       payload_checksum, status, last_evaluated_run_id)
                   SELECT :e, s.source_system, s.source_record_id, s.raw_id, s.source_file_id, s.source_file_name, :run,
                          s.error_code, s.error_reason, s.error_reason_ar, s.errors_json, s.payload, s.payload_checksum,
                          'open', :run
                   FROM OPENJSON(:j) WITH (source_system NVARCHAR(30), source_record_id NVARCHAR(100), raw_id BIGINT,
                        source_file_id INT, source_file_name NVARCHAR(260), error_code NVARCHAR(50),
                        error_reason NVARCHAR(1000), error_reason_ar NVARCHAR(1000), errors_json NVARCHAR(MAX),
                        payload NVARCHAR(MAX),
                        payload_checksum CHAR(64)) s
                   WHERE NOT EXISTS (SELECT 1 FROM qa.quarantine_record q WHERE q.raw_id = s.raw_id
                                     AND q.entity = :e AND q.status IN ('open','ignored'))"""),
                {"j": j, "run": ctx.run_id, "e": entity})
            # Older versions of the same record are superseded by this newer evaluation.
            conn.execute(text(
                """UPDATE q SET status = 'superseded', status_note = 'A newer source version was evaluated',
                          updated_at_utc = SYSUTCDATETIME()
                   FROM qa.quarantine_record q
                   JOIN OPENJSON(:j) WITH (raw_id BIGINT, source_record_id NVARCHAR(100)) s
                     ON s.source_record_id = q.source_record_id AND q.raw_id <> s.raw_id
                   WHERE q.entity = :e AND q.status IN ('open','ignored')"""), {"j": j, "e": entity})
    if accepted:
        for chunk in chunked(accepted, 4000):
            conn.execute(text(
                """UPDATE q SET status = CASE WHEN q.raw_id = s.raw_id THEN 'resolved' ELSE 'superseded' END,
                          resolved_run_id = CASE WHEN q.raw_id = s.raw_id THEN :run ELSE q.resolved_run_id END,
                          status_note = CASE WHEN q.raw_id = s.raw_id THEN 'Accepted on reprocessing'
                                             ELSE 'A newer source version was accepted' END,
                          last_evaluated_run_id = :run, updated_at_utc = SYSUTCDATETIME()
                   FROM qa.quarantine_record q
                   JOIN OPENJSON(:j) WITH (raw_id BIGINT, source_record_id NVARCHAR(100)) s
                     ON s.source_record_id = q.source_record_id
                   WHERE q.entity = :e AND q.status IN ('open','ignored')"""),
                {"j": bulk_json_param({"raw_id": a["raw_id"], "source_record_id": a["source_record_id"]} for a in chunk),
                 "run": ctx.run_id, "e": entity})


def run_staging(conn: Connection, ctx: RunContext) -> dict:
    refs = RefState(conn)
    file_versions = {r[0]: r[1] for r in conn.execute(text("SELECT file_id, file_version FROM meta.source_file"))}
    details: dict[str, dict] = {}
    for entity in ORDER:
        cands = _select_candidates(conn, ctx, entity)
        payloads = _payloads(conn, [c["raw_id"] for c in cands])
        conflicts: set[tuple[int, str]] = set()
        if entity in ("product", "payment_txn"):
            conflicts = {(r[0], r[1]) for r in conn.execute(text(
                """SELECT source_file_id, source_record_id FROM raw.source_record WHERE entity = :e
                   AND source_file_id IS NOT NULL GROUP BY source_file_id, source_record_id
                   HAVING COUNT(DISTINCT payload_checksum) > 1"""), {"e": entity})}
        parse: Callable = STG[entity]["parse"]
        parsed = []
        for c in cands:
            payload = load_payload(payloads[c["raw_id"]])
            rec, issues = parse(payload)
            issues = list(issues)
            if (c["source_file_id"], c["source_record_id"]) in conflicts:
                issues.insert(0, Issue("DUPLICATE_KEY", None,
                                       f"'{c['source_record_id']}' appears more than once in {c['source_file_name']} "
                                       "with different contents; the correct version cannot be determined.",
                                       f"المعرّف '{c['source_record_id']}' مكرر في الملف {c['source_file_name']} بمحتوى "
                                       "مختلف، ولا يمكن تحديد النسخة الصحيحة."))
            parsed.append((c, rec, issues, payloads[c["raw_id"]]))
        if entity == "payment_txn":  # payments before the refunds that reference them
            parsed.sort(key=lambda x: (x[1]["transaction_at_utc"] if x[1] else datetime.max,
                                       0 if x[1] and x[1]["transaction_type"] == "payment" else 1,
                                       x[0]["source_record_id"]))
        accepted, rejected, stale = [], [], 0
        for c, rec, issues, payload in parsed:
            if entity == "product" and rec is not None:
                rec["source_file_id"] = c["source_file_id"]
                rec["source_file_version"] = file_versions.get(c["source_file_id"]) or 0
                current = refs.products.get(rec["product_sku"])
                if current is not None and current > rec["source_file_version"] and not issues:
                    stale += 1  # an older catalog snapshot never overwrites a newer one
                    continue
            if entity == "payment_txn" and rec is not None:
                rec["source_file_id"] = c["source_file_id"]
            if rec is not None and not issues:
                refs.check(entity, rec, issues)
            for code in {i.code for i in issues}:
                ctx.record_failures[(entity, code)] += 1
            if issues:
                rejected.append({
                    "source_system": SOURCE_SYSTEM[entity], "source_record_id": c["source_record_id"],
                    "raw_id": c["raw_id"], "source_file_id": c["source_file_id"],
                    "source_file_name": c["source_file_name"], "error_code": issues[0].code,
                    "error_reason": issues[0].message[:1000], "error_reason_ar": (issues[0].message_ar or "")[:1000] or None,
                    "errors_json": json.dumps([i.to_dict() for i in issues]),
                    "payload": payload, "payload_checksum": c["payload_checksum"]})
            else:
                rec["raw_id"] = c["raw_id"]
                rec["source_checksum"] = c["payload_checksum"]
                rec["source_record_id"] = c["source_record_id"]
                refs.accept(entity, rec)
                accepted.append(rec)
        _merge_stg(conn, ctx, entity, accepted)
        _write_quarantine(conn, ctx, entity, rejected, accepted)
        st = ctx.stats[entity]
        st.rows_evaluated += len(parsed)
        st.rows_accepted += len(accepted)
        st.rows_rejected += len(rejected)
        st.rows_unchanged += stale
        ctx.evaluated[entity] += len(parsed)
        details[entity] = {"evaluated": len(parsed), "accepted": len(accepted), "rejected": len(rejected),
                           "stale_catalog_versions": stale}
    return details
