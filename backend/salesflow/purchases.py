"""Manual test purchases (development only).

A purchase is written to the SOURCE systems, exactly like real activity:
the order, its lines and (optionally) a new customer go into the operational
database; payment transactions go into a new JSON file in the landing zone.
Nothing is written to the warehouse here: the pipeline picks the purchase up,
validates it and loads it, so every rule (quarantine, reconciliation) applies.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import text

from .config import Settings, get_settings
from .db import get_engine
from .generator.io import _ser_row, _upsert_ops
from .generator.scenarios import payments_json

CENT = Decimal("0.01")
METHODS = ("mada", "credit_card", "apple_pay", "stc_pay", "cash_on_delivery")
OUTCOMES = ("paid", "failed_then_paid", "failed", "unpaid")


class PurchaseError(Exception):
    def __init__(self, code: str, message: str, message_ar: str):
        super().__init__(message)
        self.code, self.message, self.message_ar = code, message, message_ar


@dataclass
class Line:
    sku: str
    quantity: int
    discount_amount: Decimal


def create_purchase(*, customer_id: int | None, new_customer: dict | None, lines: list[Line], channel: str,
                    method: str, outcome: str, settings: Settings | None = None) -> dict:
    settings = settings or get_settings()
    if method not in METHODS or outcome not in OUTCOMES:
        raise PurchaseError("INVALID_REQUEST", "Unknown payment method or outcome.", "طريقة دفع أو نتيجة غير معروفة.")
    if method == "cash_on_delivery" and outcome != "unpaid":
        raise PurchaseError("INVALID_REQUEST", "Cash on delivery is collected at delivery, so the order starts unpaid.",
                            "الدفع عند الاستلام يُحصَّل عند التسليم، لذلك يبدأ الطلب غير مدفوع.")
    if not lines:
        raise PurchaseError("INVALID_REQUEST", "Add at least one product.", "أضف منتجاً واحداً على الأقل.")
    if len({l.sku for l in lines}) != len(lines):
        raise PurchaseError("INVALID_REQUEST", "Each product can appear only once; change its quantity instead.",
                            "كل منتج يظهر مرة واحدة فقط؛ غيّر الكمية بدلاً من تكراره.")

    # Prices come from the accepted catalog (what the store would charge right now).
    with get_engine("dw", settings).connect() as conn:
        catalog = {r[0]: (r[1], r[2], r[3]) for r in conn.execute(text(
            "SELECT product_sku, list_price, is_active, product_name FROM stg.product WHERE product_sku IN "
            "(SELECT value FROM OPENJSON(:j))"), {"j": json.dumps([l.sku for l in lines])})}
        if customer_id is not None:
            known = conn.execute(text("SELECT 1 FROM stg.customer WHERE customer_id = :c"), {"c": customer_id}).scalar()
            if not known:
                raise PurchaseError("NOT_FOUND", f"Customer {customer_id} is not in the accepted data.",
                                    f"العميل {customer_id} غير موجود في البيانات المقبولة.")
    priced = []
    for l in lines:
        if l.sku not in catalog:
            raise PurchaseError("NOT_FOUND", f"Product {l.sku} is not in the catalog.", f"المنتج {l.sku} غير موجود في الكتالوج.")
        price, active, _name = catalog[l.sku]
        if not active:
            raise PurchaseError("INVALID_REQUEST", f"Product {l.sku} is not active.", f"المنتج {l.sku} غير نشط.")
        gross = (Decimal(price) * l.quantity).quantize(CENT)
        if l.discount_amount > gross:
            raise PurchaseError("INVALID_REQUEST", f"The discount on {l.sku} is larger than the line value ({gross}).",
                                f"الخصم على {l.sku} أكبر من قيمة السطر ({gross}).")
        priced.append((l, Decimal(price).quantize(CENT), gross - l.discount_amount))
    total = sum((n for _l, _p, n in priced), Decimal("0.00"))

    ops = get_engine("ops", settings)
    with ops.begin() as conn:
        mx = conn.execute(text(
            """SELECT (SELECT MAX(order_id) FROM dbo.orders WITH (UPDLOCK, HOLDLOCK)),
                      (SELECT MAX(order_item_id) FROM dbo.order_items WITH (UPDLOCK, HOLDLOCK)),
                      (SELECT MAX(customer_id) FROM dbo.customers WITH (UPDLOCK, HOLDLOCK)),
                      (SELECT MAX(m) FROM (SELECT MAX(updated_at) m FROM dbo.customers UNION ALL
                                           SELECT MAX(updated_at) FROM dbo.orders UNION ALL
                                           SELECT MAX(updated_at) FROM dbo.order_items) x)""")).first()
        # Modification time must move forward past every existing row, or extraction would skip it.
        now = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
        if mx[3] is not None and now <= mx[3]:
            now = mx[3] + timedelta(seconds=1)
        order_id = (mx[0] or 1000) + 1
        item_id = (mx[1] or 0) + 1
        cust_rows = []
        if customer_id is None:
            customer_id = (mx[2] or 0) + 1
            nc = new_customer or {}
            cust_rows.append({"customer_id": customer_id, "customer_code": f"C{customer_id:05d}",
                              "first_name": nc.get("first_name"), "last_name": nc.get("last_name"),
                              "email": nc.get("email"), "city": nc.get("city"),
                              "created_at": now, "updated_at": now})
        status = "pending" if outcome == "failed" else "processing"
        order = {"order_id": order_id, "customer_id": customer_id, "order_placed_at": now, "status": status,
                 "currency": "SAR", "channel": channel, "created_at": now, "updated_at": now}
        items = [{"order_item_id": item_id + i, "order_id": order_id, "line_number": i + 1, "product_sku": l.sku,
                  "quantity": l.quantity, "unit_price": price, "discount_amount": l.discount_amount,
                  "created_at": now, "updated_at": now} for i, (l, price, _net) in enumerate(priced)]
        if cust_rows:
            _upsert_ops(conn, "customers", [_ser_row("customers", r) for r in cust_rows])
        _upsert_ops(conn, "orders", [_ser_row("orders", order)])
        _upsert_ops(conn, "order_items", [_ser_row("order_items", r) for r in items])

    txns = []
    at = now + timedelta(seconds=30)
    if outcome in ("failed", "failed_then_paid"):
        txns.append({"transaction_id": f"PAY-M{order_id}-1", "status": "failed", "at": at})
        at += timedelta(minutes=2)
    if outcome in ("paid", "failed_then_paid"):
        txns.append({"transaction_id": f"PAY-M{order_id}-{len(txns) + 1}", "status": "succeeded", "at": at})
    file_name = None
    if txns:
        body = [{"transaction_id": t["transaction_id"], "order_id": order_id, "transaction_type": "payment",
                 "status": t["status"], "amount": f"{total:.2f}", "currency": "SAR", "payment_method": method,
                 "transaction_at": t["at"].isoformat(timespec="seconds") + "Z", "refers_to_transaction_id": None}
                for t in txns]
        file_name = f"payments_manual_{order_id}.json"
        folder = settings.landing_dir / "payments"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / file_name).write_text(payments_json(f"payments_manual_{order_id}", body, now), encoding="utf-8")

    return {
        "order_id": order_id, "customer_id": customer_id, "new_customer": bool(cust_rows), "status": status,
        "order_placed_at_utc": now.isoformat(timespec="seconds") + "Z", "lines": len(items),
        "ordered_value": f"{total:.2f}", "payment_file": file_name,
        "transactions": [{"transaction_id": t["transaction_id"], "status": t["status"]} for t in txns],
    }
