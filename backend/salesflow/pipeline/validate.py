"""Pure record-level parsing, normalisation and validation.

Each ``parse_*`` function takes an original payload and returns
``(normalised_record | None, issues)``. Reference checks (customer / product /
order existence, refund rules) are applied by the staging step, which knows the
accepted state. No database access here, so the rules are unit-testable.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from ..config import CURRENCY
from ..timeutil import business_date, parse_ts

ORDER_STATUSES = {"pending", "processing", "shipped", "delivered", "cancelled"}
ORDER_STATUS_SYNONYMS = {"canceled": "cancelled", "complete": "delivered", "completed": "delivered"}
TXN_STATUSES = {"succeeded", "failed", "pending"}
TXN_STATUS_SYNONYMS = {"success": "succeeded", "successful": "succeeded", "captured": "succeeded",
                       "declined": "failed", "error": "failed"}
TXN_TYPES = {"payment", "refund"}
TXN_TYPE_SYNONYMS = {"charge": "payment", "capture": "payment"}
MIN_DATE, MAX_DATE = date(2025, 1, 1), date(2027, 12, 31)  # dw.DimDate coverage

# Catalogue of record-level checks: code -> (human name, entities it applies to)
CHECKS: dict[str, tuple[str, tuple[str, ...]]] = {
    "SCHEMA_INVALID_RECORD": ("Record has the expected structure", ("product", "payment_txn")),
    "REQUIRED_FIELD": ("Required fields are present", ("customer", "product", "order", "order_item", "payment_txn")),
    "INVALID_TYPE": ("Fields have valid data types", ("customer", "product", "order", "order_item", "payment_txn")),
    "INVALID_FORMAT": ("Fields have a valid format", ("customer",)),
    "DUPLICATE_KEY": ("Business key is unique within a delivery", ("product", "payment_txn")),
    "INVALID_QUANTITY": ("Quantity is a positive integer", ("order_item",)),
    "NEGATIVE_AMOUNT": ("Amounts are not negative", ("product", "order_item", "payment_txn")),
    "INVALID_AMOUNT": ("Transaction amount is greater than zero", ("payment_txn",)),
    "INVALID_PRECISION": ("Money has at most 2 decimal places", ("product", "order_item", "payment_txn")),
    "INVALID_DISCOUNT": ("Discount does not exceed line gross value", ("order_item",)),
    "INVALID_STATUS": ("Status is a known value", ("order", "payment_txn")),
    "INVALID_TRANSACTION_TYPE": ("Transaction type is payment or refund", ("payment_txn",)),
    "UNSUPPORTED_CURRENCY": ("Currency is SAR", ("product", "order", "payment_txn")),
    "IMPOSSIBLE_TIMESTAMP": ("Timestamps are in a possible order", ("customer", "order", "order_item", "payment_txn")),
    "DATE_OUT_OF_RANGE": ("Business date is inside the calendar", ("order", "payment_txn")),
    "MISSING_CUSTOMER_REF": ("Order references an accepted customer", ("order",)),
    "MISSING_ORDER_REF": ("References an accepted order", ("order_item", "payment_txn")),
    "MISSING_PRODUCT_REF": ("Order line references a catalog product", ("order_item",)),
    "INVALID_REFUND_REF": ("Refund references a successful payment of the same order", ("payment_txn",)),
    "REFUND_EXCEEDS_COLLECTION": ("Refunds do not exceed the related payment", ("payment_txn",)),
}


@dataclass(frozen=True)
class Issue:
    code: str
    field: str | None
    message: str
    message_ar: str = ""

    def to_dict(self) -> dict:
        return {"code": self.code, "field": self.field, "message": self.message, "message_ar": self.message_ar}


class _P:
    """Accumulates issues while parsing one record."""

    def __init__(self, payload: dict):
        self.p = payload
        self.issues: list[Issue] = []

    def add(self, code: str, field: str | None, message: str, ar: str = "") -> None:
        self.issues.append(Issue(code, field, message, ar))

    def present(self, f: str) -> bool:
        v = self.p.get(f)
        return not (v is None or (isinstance(v, str) and not v.strip()))

    def text(self, f: str, required: bool = True, max_len: int = 200) -> str | None:
        if not self.present(f):
            if required:
                self.add("REQUIRED_FIELD", f, f"'{f}' is required.", f"الحقل '{f}' مطلوب.")
            return None
        v = self.p[f]
        if not isinstance(v, str):
            self.add("INVALID_TYPE", f, f"'{f}' must be text; got {type(v).__name__}.", f"يجب أن يكون الحقل '{f}' نصاً، والقيمة من نوع {type(v).__name__}.")
            return None
        v = v.strip()
        if len(v) > max_len:
            self.add("INVALID_TYPE", f, f"'{f}' is longer than {max_len} characters.", f"الحقل '{f}' أطول من {max_len} حرفاً.")
            return None
        return v

    def integer(self, f: str, required: bool = True) -> int | None:
        if not self.present(f):
            if required:
                self.add("REQUIRED_FIELD", f, f"'{f}' is required.", f"الحقل '{f}' مطلوب.")
            return None
        v = self.p[f]
        if isinstance(v, bool) or isinstance(v, float):
            self.add("INVALID_TYPE", f, f"'{f}' must be an integer; got {v!r}.", f"يجب أن يكون الحقل '{f}' عدداً صحيحاً، والقيمة {v!r}.")
            return None
        if isinstance(v, int):
            return v
        if isinstance(v, Decimal) and v == v.to_integral_value():
            return int(v)
        if isinstance(v, str) and v.strip().lstrip("-").isdigit():
            return int(v.strip())
        self.add("INVALID_TYPE", f, f"'{f}' must be an integer; got {v!r}.", f"يجب أن يكون الحقل '{f}' عدداً صحيحاً، والقيمة {v!r}.")
        return None

    def money(self, f: str, required: bool = True, allow_zero: bool = True) -> Decimal | None:
        if not self.present(f):
            if required:
                self.add("REQUIRED_FIELD", f, f"'{f}' is required.", f"الحقل '{f}' مطلوب.")
            return None
        v = self.p[f]
        if isinstance(v, (bool, float)):
            self.add("INVALID_TYPE", f, f"'{f}' must be a decimal number; got {v!r}.", f"يجب أن يكون الحقل '{f}' رقماً عشرياً، والقيمة {v!r}.")
            return None
        try:
            d = Decimal(str(v).strip()) if isinstance(v, (str, int, Decimal)) else None
        except InvalidOperation:
            d = None
        if d is None or not d.is_finite():
            self.add("INVALID_TYPE", f, f"'{f}' must be a decimal number; got {v!r}.", f"يجب أن يكون الحقل '{f}' رقماً عشرياً، والقيمة {v!r}.")
            return None
        if d.normalize().as_tuple().exponent < -2:  # type: ignore[operator]
            self.add("INVALID_PRECISION", f, f"'{f}' has more than 2 decimal places ({v}).", f"الحقل '{f}' فيه أكثر من خانتين عشريتين ({v}).")
            return None
        if d < 0:
            self.add("NEGATIVE_AMOUNT", f, f"'{f}' must not be negative ({d}).", f"لا يجوز أن يكون الحقل '{f}' سالباً ({d}).")
            return None
        if d == 0 and not allow_zero:
            self.add("INVALID_AMOUNT", f, f"'{f}' must be greater than zero.", f"يجب أن يكون الحقل '{f}' أكبر من صفر.")
            return None
        return d.quantize(Decimal("0.01"))

    def ts(self, f: str, required: bool = True) -> datetime | None:
        if not self.present(f):
            if required:
                self.add("REQUIRED_FIELD", f, f"'{f}' is required.", f"الحقل '{f}' مطلوب.")
            return None
        try:
            return parse_ts(self.p[f])
        except (ValueError, TypeError):
            self.add("INVALID_TYPE", f, f"'{f}' is not an ISO-8601 timestamp: {self.p[f]!r}.", f"الحقل '{f}' ليس تاريخاً ووقتاً بصيغة ISO-8601: {self.p[f]!r}.")
            return None

    def currency(self, f: str = "currency") -> str | None:
        c = self.text(f, max_len=10)
        if c is None:
            return None
        c = c.upper()
        if c != CURRENCY:
            self.add("UNSUPPORTED_CURRENCY", f, f"Currency '{c}' is not supported; only {CURRENCY} is accepted.", f"العملة '{c}' غير مدعومة، والمقبول {CURRENCY} فقط.")
            return None
        return c

    def enum(self, f: str, allowed: set[str], synonyms: dict[str, str], code: str) -> str | None:
        v = self.text(f, max_len=30)
        if v is None:
            return None
        n = synonyms.get(v.lower(), v.lower())
        if n not in allowed:
            self.add(code, f, f"'{v}' is not a valid {f}; expected one of {', '.join(sorted(allowed))}.", f"القيمة '{v}' غير صالحة للحقل {f}، والقيم المسموحة: {', '.join(sorted(allowed))}.")
            return None
        return n

    def in_calendar(self, f: str, ts: datetime | None) -> None:
        if ts is not None and not (MIN_DATE <= business_date(ts) <= MAX_DATE):
            self.add("DATE_OUT_OF_RANGE", f, f"'{f}' business date {business_date(ts)} is outside "
                                             f"{MIN_DATE}..{MAX_DATE}.",
                     f"تاريخ العمل {business_date(ts)} في الحقل '{f}' خارج النطاق {MIN_DATE}..{MAX_DATE}.")


def _created_updated(p: _P) -> tuple[datetime | None, datetime | None]:
    c, u = p.ts("created_at"), p.ts("updated_at")
    if c and u and u < c:
        p.add("IMPOSSIBLE_TIMESTAMP", "updated_at", f"updated_at ({u}) is earlier than created_at ({c}).", f"وقت التعديل ({u}) أسبق من وقت الإنشاء ({c}).")
    return c, u


def parse_customer(payload: dict) -> tuple[dict | None, list[Issue]]:
    p = _P(payload)
    cid = p.integer("customer_id")
    code = p.text("customer_code", max_len=20)
    first, last = p.text("first_name", False, 100), p.text("last_name", False, 100)
    email = p.text("email", max_len=200)
    if email and ("@" not in email or email.startswith("@") or email.endswith("@")):
        p.add("INVALID_FORMAT", "email", f"'{email}' is not a valid email address.", f"'{email}' ليس بريداً إلكترونياً صالحاً.")
    full = " ".join(x for x in (first, last) if x)
    if not full and not any(i.field in ("first_name", "last_name") for i in p.issues):
        p.add("REQUIRED_FIELD", "first_name", "Customer must have a first or last name.", "يجب أن يكون للعميل اسم أول أو اسم عائلة.")
    city = p.text("city", False, 100)
    created, updated = _created_updated(p)
    if p.issues:
        return None, p.issues
    return {"customer_id": cid, "customer_code": code, "first_name": first, "last_name": last, "full_name": full,
            "email": email.lower(), "city": city, "created_at_utc": created, "updated_at_utc": updated}, []


def parse_product(payload: object) -> tuple[dict | None, list[Issue]]:
    if not isinstance(payload, dict) or "_error" in payload:
        reason = payload.get("_error") if isinstance(payload, dict) else "record is not an object"
        return None, [Issue("SCHEMA_INVALID_RECORD", None, f"Malformed catalog row: {reason}.", f"صف كتالوج غير سليم: {reason}.")]
    p = _P(payload)
    sku = p.text("product_sku", max_len=40)
    name = p.text("product_name")
    cat = p.text("category", max_len=100)
    price = p.money("list_price")
    cur = p.currency()
    active_raw = (p.text("is_active", max_len=10) or "").lower()
    active = {"true": True, "1": True, "yes": True, "false": False, "0": False, "no": False}.get(active_raw)
    if active is None and active_raw:
        p.add("INVALID_TYPE", "is_active", f"is_active must be true/false; got '{active_raw}'.", f"يجب أن تكون قيمة is_active إما true أو false، والقيمة '{active_raw}'.")
    updated = p.ts("updated_at", required=False)
    if p.issues:
        return None, p.issues
    return {"product_sku": sku.upper(), "product_name": name, "category": cat, "list_price": price,
            "currency": cur, "is_active": bool(active), "source_updated_at_utc": updated}, []


def parse_order(payload: dict) -> tuple[dict | None, list[Issue]]:
    p = _P(payload)
    oid = p.integer("order_id")
    cid = p.integer("customer_id")
    placed = p.ts("order_placed_at")
    status = p.enum("status", ORDER_STATUSES, ORDER_STATUS_SYNONYMS, "INVALID_STATUS")
    cur = p.currency()
    channel = p.text("channel", False, 20)
    created, updated = _created_updated(p)
    if placed and updated and updated < placed:
        p.add("IMPOSSIBLE_TIMESTAMP", "updated_at", f"updated_at ({updated}) is earlier than order_placed_at ({placed}).", f"وقت التعديل ({updated}) أسبق من وقت إنشاء الطلب ({placed}).")
    p.in_calendar("order_placed_at", placed)
    if p.issues:
        return None, p.issues
    return {"order_id": oid, "customer_id": cid, "order_placed_at_utc": placed, "order_status": status,
            "currency": cur, "channel": channel.lower() if channel else None,
            "created_at_utc": created, "updated_at_utc": updated}, []


def parse_order_item(payload: dict) -> tuple[dict | None, list[Issue]]:
    p = _P(payload)
    iid = p.integer("order_item_id")
    oid = p.integer("order_id")
    line = p.integer("line_number")
    sku = p.text("product_sku", max_len=40)
    qty = p.integer("quantity")
    if qty is not None and qty <= 0:
        p.add("INVALID_QUANTITY", "quantity", f"quantity must be a positive integer; got {qty}.", f"يجب أن تكون الكمية عدداً صحيحاً موجباً، والقيمة {qty}.")
        qty = None
    price = p.money("unit_price")
    disc = p.money("discount_amount", required=False)
    disc = Decimal("0.00") if disc is None and not p.present("discount_amount") else disc
    if qty is not None and price is not None and disc is not None and disc > qty * price:
        p.add("INVALID_DISCOUNT", "discount_amount",
              f"discount_amount {disc} exceeds the line gross value {qty * price:.2f}.",
              f"الخصم {disc} أكبر من القيمة الإجمالية للسطر {qty * price:.2f}.")
    created, updated = _created_updated(p)
    if p.issues:
        return None, p.issues
    return {"order_item_id": iid, "order_id": oid, "line_number": line, "product_sku": sku.upper(),
            "quantity": qty, "unit_price": price, "discount_amount": disc,
            "created_at_utc": created, "updated_at_utc": updated}, []


def parse_payment(payload: object) -> tuple[dict | None, list[Issue]]:
    if not isinstance(payload, dict):
        return None, [Issue("SCHEMA_INVALID_RECORD", None,
                            f"Transaction record must be a JSON object; got {type(payload).__name__}.",
                            f"يجب أن يكون سجل العملية كائن JSON، والقيمة من نوع {type(payload).__name__}.")]
    p = _P(payload)
    tid = p.text("transaction_id", max_len=40)
    oid = p.integer("order_id")
    ttype = p.enum("transaction_type", TXN_TYPES, TXN_TYPE_SYNONYMS, "INVALID_TRANSACTION_TYPE")
    status = p.enum("status", TXN_STATUSES, TXN_STATUS_SYNONYMS, "INVALID_STATUS")
    amount = p.money("amount", allow_zero=False)
    cur = p.currency()
    method = p.text("payment_method", False, 30)
    at = p.ts("transaction_at")
    p.in_calendar("transaction_at", at)
    refers = p.text("refers_to_transaction_id", False, 40)
    if ttype == "refund" and not refers:
        p.add("INVALID_REFUND_REF", "refers_to_transaction_id", "A refund must reference the payment it refunds.", "يجب أن يشير الاسترداد إلى عملية الدفع التي يستردها.")
    if p.issues:
        return None, p.issues
    return {"transaction_id": tid, "order_id": oid, "transaction_type": ttype, "transaction_status": status,
            "amount": amount, "currency": cur, "payment_method": method.lower() if method else None,
            "transaction_at_utc": at, "refers_to_transaction_id": refers if ttype == "refund" else None}, []
