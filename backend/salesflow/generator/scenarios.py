"""Deterministic incremental / failure scenarios layered on the clean baseline.

Each scenario is a set of operational-database upserts and/or landing-zone file
deliveries, plus machine-readable expected outcomes. Scenario modification
timestamps increase with the scenario number (a source system's clock only
moves forward), so scenarios must be applied in id order.
"""

from __future__ import annotations

import csv
import io
import json
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from ..timeutil import riyadh_to_utc
from .baseline import CURRENCY, Baseline, Txn, money
from .reference import RefState, delta

PRODUCT_COLUMNS = ["product_sku", "product_name", "category", "list_price", "currency", "is_active", "updated_at"]


@dataclass
class Scenario:
    id: str
    title: str
    description: str
    depends_on: list[str] = field(default_factory=list)
    steps: list[dict] = field(default_factory=lambda: [{"action": "run", "mode": "incremental"}])
    ops_upserts: dict[str, list[dict]] = field(default_factory=lambda: {"customers": [], "orders": [], "order_items": []})
    files: dict[str, str] = field(default_factory=dict)  # landing-relative path -> content
    expected: dict = field(default_factory=dict)

    def manifest_entry(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "depends_on": self.depends_on,
            "steps": self.steps,
            "ops_changes": {k: len(v) for k, v in self.ops_upserts.items()},
            "files": sorted(self.files),
            "expected": self.expected,
        }


def products_csv(rows: list[dict], columns: list[str] = PRODUCT_COLUMNS) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(columns)
    for p in rows:
        rec = {
            **p,
            "list_price": f"{p['list_price']:.2f}" if isinstance(p.get("list_price"), Decimal) else p.get("list_price"),
            "is_active": ("true" if p["is_active"] else "false") if isinstance(p.get("is_active"), bool) else p.get("is_active"),
            "updated_at": p["updated_at"].isoformat(timespec="seconds") + "Z" if isinstance(p.get("updated_at"), datetime) else p.get("updated_at"),
        }
        w.writerow([rec.get(c, "") for c in columns])
    return buf.getvalue()


def payments_json(batch_id: str, txns: list[dict], generated_at: datetime) -> str:
    doc = {
        "schema_version": "1.0",
        "source_system": "payment_gateway",
        "batch_id": batch_id,
        "generated_at": generated_at.isoformat(timespec="seconds") + "Z",
        "transactions": txns,
    }
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"


def baseline_state(b: Baseline) -> RefState:
    return RefState(
        customers={c["customer_id"]: c for c in b.customers},
        products={p["product_sku"]: p for p in b.products},
        orders={o["order_id"]: o for o in b.orders},
        items={i["order_item_id"]: i for i in b.items},
        txns={t.transaction_id: t.to_json() for t in b.files_txns},
    )


class _Builder:
    def __init__(self, b: Baseline):
        self.b = b
        self.cfg = b.config
        self.base = baseline_state(b)
        self.max_order = max(o["order_id"] for o in b.orders)
        self.max_item = max(i["order_item_id"] for i in b.items)
        self.max_cust = max(c["customer_id"] for c in b.customers)
        self.active_products = [p for p in b.products if p["is_active"]]

    def rng(self, n: int) -> random.Random:
        return random.Random(self.cfg.seed * 100 + n)

    def new_order(self, rng: random.Random, oid: int, item_start: int, cid: int, placed: datetime,
                  n_lines: int | None = None, status: str = "processing") -> tuple[dict, list[dict]]:
        order = {"order_id": oid, "customer_id": cid, "order_placed_at": placed, "status": status,
                 "currency": CURRENCY, "channel": rng.choice(["app", "web"]), "created_at": placed, "updated_at": placed}
        n_lines = n_lines or rng.choice([1, 1, 2, 2, 3])
        prods = rng.sample(self.active_products, n_lines)
        items = []
        for ln, p in enumerate(prods, start=1):
            items.append({"order_item_id": item_start + ln - 1, "order_id": oid, "line_number": ln,
                          "product_sku": p["product_sku"], "quantity": rng.choice([1, 1, 2]),
                          "unit_price": p["list_price"], "discount_amount": Decimal("0.00"),
                          "created_at": placed, "updated_at": placed})
        return order, items

    @staticmethod
    def order_net(items: list[dict]) -> Decimal:
        return sum((i["quantity"] * i["unit_price"] - i["discount_amount"] for i in items), Decimal("0.00"))

    # ------------------------------------------------------------------ s01
    def s01(self) -> Scenario:
        s = Scenario("s01_initial_load", "Initial load",
                     "Clean baseline: three months of orders, catalog v001 and daily payment files. "
                     "Loaded with an initial (full) run.",
                     steps=[{"action": "run", "mode": "initial"}])
        late = [t for t in self.b.txns if t.late]
        s.expected = {
            "warehouse_totals": self.base.summary(),
            "quarantine_open": {},
            "notes": [f"{len(late)} cash-on-delivery collections from October are withheld until s04_late_payments.",
                      "Order 1001: GMV 500.00, collections 500.00, refunds 100.00, net collections 400.00."],
        }
        return s

    # ------------------------------------------------------------------ s02
    def s02(self) -> Scenario:
        rng = self.rng(2)
        s = Scenario("s02_new_orders", "New orders",
                     "120 new orders (10 from new customers) placed 1-2 Nov 2026, with their card payments.")
        clock = riyadh_to_utc(datetime(2026, 11, 1, 0, 0))
        new_customers = []
        for k in range(10):
            cid = self.max_cust + 1 + k
            created = clock + timedelta(minutes=30 * k)
            new_customers.append({"customer_id": cid, "customer_code": f"C{cid:05d}", "first_name": "New",
                                  "last_name": f"Customer{k + 1}", "email": f"new.customer{cid}@example.com",
                                  "city": rng.choice(["Riyadh", "Jeddah", "Dammam"]), "created_at": created,
                                  "updated_at": created})
        orders, items, txns = [], [], []
        item_id = 1_000_001
        existing_ids = sorted(self.base.customers)
        for k in range(120):
            oid = self.max_order + 1 + k
            placed = clock + timedelta(minutes=5 + k * 23 + rng.randrange(10))
            cid = new_customers[k % 10]["customer_id"] if k < 20 else rng.choice(existing_ids)
            o, its = self.new_order(rng, oid, item_id, cid, placed)
            item_id += len(its)
            orders.append(o)
            items += its
            txns.append(Txn(oid, "payment", "succeeded", self.order_net(its), rng.choice(["mada", "apple_pay", "credit_card"]),
                            placed + timedelta(minutes=1), transaction_id=f"PAY-S02-{k + 1:04d}").to_json())
        s.ops_upserts = {"customers": new_customers, "orders": orders, "order_items": items}
        by_day: dict[str, list[dict]] = {}
        for t in txns:
            day = (datetime.fromisoformat(t["transaction_at"][:-1]) + timedelta(hours=3)).date().isoformat()
            by_day.setdefault(day, []).append(t)
        for day, ts in sorted(by_day.items()):
            s.files[f"payments/payments_{day}.json"] = payments_json(f"payments_{day}", ts, clock + timedelta(days=2))
        after = self.base.copy().upsert(customers=new_customers, orders=orders, items=items, txns=txns)
        s.expected = {"warehouse_delta": delta(self.base.summary(), after.summary()), "quarantine_open": {}}
        return s

    # ------------------------------------------------------------------ s03
    def s03(self) -> Scenario:
        rng = self.rng(3)
        s = Scenario("s03_order_updates", "Existing order updates",
                     "Status changes on existing orders (shipped -> delivered, unpaid cash-on-delivery orders "
                     "cancelled) and quantity corrections on order lines.")
        clock = riyadh_to_utc(datetime(2026, 11, 3, 1, 0))
        shipped =sorted(o["order_id"] for o in self.b.orders if o["status"] == "shipped")
        unpaid_cod = sorted(oid for oid, m in self.b.meta.items()
                            if m["method"] == "cash_on_delivery" and m["status"] in ("processing", "shipped")
                            and not m["has_txns"])
        to_deliver = rng.sample(shipped, min(25, len(shipped)))
        unpaid_cod = [o for o in unpaid_cod if o not in to_deliver]
        to_cancel = rng.sample(unpaid_cod, min(15, len(unpaid_cod) // 2))
        items_by_order: dict[int, list[dict]] = {}
        for it in self.b.items:
            items_by_order.setdefault(it["order_id"], []).append(it)
        qty_cands = [o for o in unpaid_cod if o not in to_cancel
                     and any(i["quantity"] >= 2 for i in items_by_order[o])]
        to_fix = rng.sample(qty_cands, min(5, len(qty_cands)))
        orders, items = [], []
        for k, oid in enumerate(sorted(to_deliver)):
            orders.append({**self.base.orders[oid], "status": "delivered", "updated_at": clock + timedelta(minutes=k)})
        for k, oid in enumerate(sorted(to_cancel)):
            orders.append({**self.base.orders[oid], "status": "cancelled", "updated_at": clock + timedelta(minutes=30 + k)})
        for k, oid in enumerate(sorted(to_fix)):
            line = next(i for i in items_by_order[oid] if i["quantity"] >= 2)
            items.append({**line, "quantity": line["quantity"] - 1, "updated_at": clock + timedelta(minutes=60 + k)})
        s.ops_upserts = {"customers": [], "orders": orders, "order_items": items}
        after = self.base.copy().upsert(orders=orders, items=items)
        s.expected = {
            "warehouse_delta": delta(self.base.summary(), after.summary()),
            "dim_order_updated": len(orders),
            "sales_lines_updated": len(items),
            "delivered_order_ids": sorted(to_deliver),
            "cancelled_order_ids": sorted(to_cancel),
            "quantity_corrected_item_ids": sorted(i["order_item_id"] for i in items),
            "quarantine_open": {},
        }
        return s

    # ------------------------------------------------------------------ s04
    def s04(self) -> Scenario:
        s = Scenario("s04_late_payments", "Late-arriving payments",
                     "A payment file delivered in November containing cash-on-delivery collections dated in "
                     "October. Collections are reported on the transaction date, so October totals increase.")
        late = sorted((t for t in self.b.txns if t.late), key=lambda t: t.at)
        js = [t.to_json() for t in late]
        s.files["payments/payments_2026-10_late_cod.json"] = payments_json(
            "payments_2026-10_late_cod", js, riyadh_to_utc(datetime(2026, 11, 3, 2, 0)))
        after = self.base.copy().upsert(txns=js)
        amount = sum((t.amount for t in late), Decimal("0.00"))
        s.expected = {
            "warehouse_delta": delta(self.base.summary(), after.summary()),
            "late_transaction_ids": [t.transaction_id for t in late],
            "late_collections_amount": f"{amount:.2f}",
            "late_collections_month": "2026-10",
            "quarantine_open": {},
        }
        return s

    # ------------------------------------------------------------------ s05
    def s05(self) -> Scenario:
        s = Scenario("s05_duplicate_file", "Duplicate file delivery",
                     "Two baseline payment files are re-delivered under new names (identical bytes) and one "
                     "baseline file is re-delivered under its original name with different content.")
        base_files = baseline_payment_files(self.b)
        names = sorted(base_files)
        dup1, dup2, conflict = names[len(names) // 6], names[len(names) // 2], names[len(names) // 4]
        s.files[f"payments/{dup1.replace('.json', '_resend.json')}"] = base_files[dup1]
        s.files[f"payments/{dup2.replace('.json', ' (1).json')}"] = base_files[dup2]
        doc = json.loads(base_files[conflict])
        doc["transactions"][0]["amount"] = f"{Decimal(doc['transactions'][0]['amount']) + 1:.2f}"
        s.files[f"payments/{conflict}"] = json.dumps(doc, indent=2) + "\n"
        s.expected = {
            "warehouse_delta": delta(self.base.summary(), self.base.summary()),
            "files": {"duplicate": 2, "conflict": 1},
            "conflict_file": conflict,
            "quarantine_open": {},
        }
        return s

    # ------------------------------------------------------------------ s06
    def s06(self) -> Scenario:
        rng = self.rng(6)
        s = Scenario("s06_catalog_price_update", "Catalog price update",
                     "Catalog snapshot v002 raises the list price of 25 products, including SKU-00001 "
                     "(100.00 -> 120.00). Historical order lines must keep their recorded unit price.")
        changed = ["SKU-00001"] + rng.sample([p["product_sku"] for p in self.b.products[2:]], 24)
        at = riyadh_to_utc(datetime(2026, 11, 2, 0, 0))
        rows = []
        for p in self.b.products:
            if p["product_sku"] == "SKU-00001":
                rows.append({**p, "list_price": money(120), "updated_at": at})
            elif p["product_sku"] in changed:
                rows.append({**p, "list_price": money(p["list_price"] * Decimal(rng.choice(["1.10", "1.15", "1.25"]))), "updated_at": at})
            else:
                rows.append(p)
        s.files["products/products_v002.csv"] = products_csv(rows)
        s.expected = {
            "warehouse_delta": delta(self.base.summary(), self.base.summary()),
            "dim_product_updated": len(changed),
            "changed_skus": sorted(changed),
            "sku_00001_list_price": "120.00",
            "order_1001_gross_merchandise_value": "500.00",
            "quarantine_open": {},
        }
        self.catalog_v2 = rows
        return s

    # ------------------------------------------------------------------ s07
    def s07(self) -> Scenario:
        rng = self.rng(7)
        s = Scenario("s07_invalid_product_refs", "Invalid product references",
                     "Six new orders each contain one valid line and one line for a SKU that is not in the "
                     "catalog yet (SKU-90001..SKU-90006). Unknown-product lines are quarantined.")
        clock = riyadh_to_utc(datetime(2026, 11, 3, 10, 0))
        orders, items, valid_items, txns = [], [], [], []
        item_id = 1_002_001
        for k in range(6):
            oid = self.max_order + 201 + k
            placed = clock + timedelta(minutes=17 * k)
            o, its = self.new_order(rng, oid, item_id, rng.choice(sorted(self.base.customers)), placed, n_lines=1)
            bad = {**its[0], "order_item_id": item_id + 1, "line_number": 2, "product_sku": f"SKU-9000{k + 1}",
                   "unit_price": money(rng.choice([59, 129, 249])), "quantity": 1}
            item_id += 2
            orders.append(o)
            items += [its[0], bad]
            valid_items.append(its[0])
            txns.append(Txn(oid, "payment", "succeeded", self.order_net([its[0], bad]), "mada",
                            placed + timedelta(minutes=1), transaction_id=f"PAY-S07-{k + 1:04d}").to_json())
        s.ops_upserts = {"customers": [], "orders": orders, "order_items": items}
        s.files["payments/payments_2026-11-03_s07.json"] = payments_json("payments_2026-11-03_s07", txns, clock + timedelta(days=1))
        after = self.base.copy().upsert(orders=orders, items=valid_items, txns=txns)
        s.expected = {
            "warehouse_delta": delta(self.base.summary(), after.summary()),
            "quarantine_open": {"MISSING_PRODUCT_REF": 6},
            "quarantined_item_ids": sorted(i["order_item_id"] for i in items if i not in valid_items),
        }
        self.s07_items = items
        self.s07_accepted = {"orders": orders, "items": valid_items, "txns": txns}
        return s

    # ------------------------------------------------------------------ s08
    def s08(self) -> Scenario:
        rng = self.rng(8)
        s = Scenario("s08_invalid_values", "Invalid quantities, amounts and statuses",
                     "Operational rows and a payment file containing invalid quantities, negative amounts, "
                     "bad statuses, unsupported currency, missing references, impossible timestamps, "
                     "invalid refunds and over-refunds, mixed with valid records.")
        clock = riyadh_to_utc(datetime(2026, 11, 4, 9, 0))
        c_bad_ts, c_no_email = self.max_cust + 101, self.max_cust + 102
        customers = [
            {"customer_id": c_bad_ts, "customer_code": f"C{c_bad_ts:05d}", "first_name": "Clock", "last_name": "Skew",
             "email": f"clock.skew{c_bad_ts}@example.com", "city": "Riyadh",
             "created_at": clock + timedelta(days=1), "updated_at": clock},
            {"customer_id": c_no_email, "customer_code": f"C{c_no_email:05d}", "first_name": "No", "last_name": "Email",
             "email": None, "city": "Jeddah", "created_at": clock, "updated_at": clock},
        ]
        good_cust = sorted(self.base.customers)[10]
        orders, items, valid_orders, valid_items = [], [], [], []
        item_id = 1_003_001
        base = self.max_order + 300

        def add(k: int, *, cid=good_cust, status="processing", currency=CURRENCY, bad_ts=False,
                bad_line: dict | None = None, order_valid=True):
            nonlocal item_id
            placed = clock + timedelta(minutes=10 * k)
            o, its = self.new_order(rng, base + k, item_id, cid, placed, n_lines=1)
            o.update(status=status, currency=currency)
            if bad_ts:
                o["updated_at"] = placed - timedelta(hours=5)
            item_id += 1
            lines = list(its)
            if bad_line is not None:
                lines.append({**its[0], "order_item_id": item_id, "line_number": 2, **bad_line})
                item_id += 1
            orders.append(o)
            items.extend(lines)
            if order_valid:
                valid_orders.append(o)
                valid_items.append(its[0])
            return o, lines

        o301, _ = add(1, bad_line={"quantity": 0})                      # INVALID_QUANTITY
        o302, _ = add(2, bad_line={"quantity": -2})                     # INVALID_QUANTITY
        add(3, bad_line={"unit_price": money(-50)})                     # NEGATIVE_AMOUNT
        add(4, bad_line={"quantity": 1, "discount_amount": money(99999)})  # INVALID_DISCOUNT
        add(5, status="teleported", order_valid=False)                  # INVALID_STATUS (+ line MISSING_ORDER_REF)
        add(6, currency="USD", order_valid=False)                       # UNSUPPORTED_CURRENCY (+ line)
        add(7, cid=999999, order_valid=False)                           # MISSING_CUSTOMER_REF (+ line)
        add(8, cid=c_bad_ts, order_valid=False)                         # MISSING_CUSTOMER_REF (+ line)
        add(9, bad_ts=True, order_valid=False)                          # IMPOSSIBLE_TIMESTAMP (+ line)
        s.ops_upserts = {"customers": customers, "orders": orders, "order_items": items}

        # Payment file with a mix of valid and invalid transactions.
        paid = sorted(oid for oid, m in self.b.meta.items()
                      if m["status"] == "delivered" and m["method"] not in ("cash_on_delivery",) and oid > 1002)
        refund_free = [oid for oid in paid if not any(t.order_id == oid and t.transaction_type == "refund" for t in self.b.txns)]
        over_order = rng.choice(refund_free)
        over_pay = next(t for t in self.b.txns if t.order_id == over_order and t.transaction_type == "payment" and t.status == "succeeded")
        early_order = rng.choice(refund_free)
        early_placed = self.b.meta[early_order]["placed"]
        t = lambda **kw: {"transaction_id": None, "order_id": None, "transaction_type": "payment", "status": "succeeded",
                          "amount": "10.00", "currency": CURRENCY, "payment_method": "mada",
                          "transaction_at": (clock + timedelta(hours=2)).isoformat(timespec="seconds") + "Z",
                          "refers_to_transaction_id": None, **kw}
        valid_txns = [
            t(transaction_id="PAY-S08-0001", order_id=o301["order_id"], amount="50.00"),
            t(transaction_id="PAY-S08-0002", order_id=o302["order_id"], amount="75.00"),
        ]
        txns = valid_txns + [
            t(transaction_id="PAY-S08-0003", order_id=base + 3, amount="-75.00"),                    # NEGATIVE_AMOUNT
            t(transaction_id="PAY-S08-0004", order_id=base + 4, currency="USD"),                      # UNSUPPORTED_CURRENCY
            t(transaction_id="PAY-S08-0005", order_id=o301["order_id"], status="maybe"),              # INVALID_STATUS
            t(transaction_id="RFD-S08-0006", order_id=o302["order_id"], transaction_type="refund",
              refers_to_transaction_id="PAY-DOES-NOT-EXIST"),                                         # INVALID_REFUND_REF
            t(transaction_id="RFD-S08-0007", order_id=over_order, transaction_type="refund",
              amount=f"{over_pay.amount + 50:.2f}", refers_to_transaction_id=over_pay.transaction_id),  # REFUND_EXCEEDS_COLLECTION
            t(transaction_id="PAY-S08-0008", order_id=early_order,
              transaction_at=(early_placed - timedelta(days=2)).isoformat(timespec="seconds") + "Z"),  # IMPOSSIBLE_TIMESTAMP
            t(transaction_id="PAY-S08-0009", order_id=o301["order_id"], amount="11.00"),              # DUPLICATE_KEY
            t(transaction_id="PAY-S08-0009", order_id=o301["order_id"], amount="12.00"),
            t(transaction_id="PAY-S08-0010", order_id=None),                                          # REQUIRED_FIELD
            t(transaction_id="PAY-S08-0011", order_id=888888),                                        # MISSING_ORDER_REF
            t(transaction_id="PAY-S08-0012", order_id=o302["order_id"], amount="10.555"),             # INVALID_PRECISION
            t(transaction_id="PAY-S08-0013", order_id=o302["order_id"], transaction_type="chargeback"),  # INVALID_TRANSACTION_TYPE
        ]
        s.files["payments/payments_2026-11-04_s08.json"] = payments_json("payments_2026-11-04_s08", txns, clock + timedelta(days=1))
        after = self.base.copy().upsert(orders=valid_orders, items=valid_items, txns=valid_txns)
        s.expected = {
            "warehouse_delta": delta(self.base.summary(), after.summary()),
            "quarantine_open": {
                "IMPOSSIBLE_TIMESTAMP": 3,       # customer, order, payment
                "REQUIRED_FIELD": 2,             # customer email, payment order_id
                "INVALID_QUANTITY": 2,
                "NEGATIVE_AMOUNT": 2,            # line unit price, payment amount
                "INVALID_DISCOUNT": 1,
                "INVALID_STATUS": 2,             # order, payment
                "UNSUPPORTED_CURRENCY": 2,       # order, payment
                "MISSING_CUSTOMER_REF": 2,
                "MISSING_ORDER_REF": 6,          # 5 lines of rejected orders + 1 payment
                "INVALID_REFUND_REF": 1,
                "REFUND_EXCEEDS_COLLECTION": 1,
                "DUPLICATE_KEY": 1,
                "INVALID_PRECISION": 1,
                "INVALID_TRANSACTION_TYPE": 1,
            },
        }
        return s

    # ------------------------------------------------------------------ s09
    def s09(self) -> Scenario:
        s = Scenario("s09_schema_invalid", "Schema-invalid input",
                     "A catalog file with a renamed/missing column, a truncated JSON file, a JSON file with the "
                     "wrong structure, and a well-formed file containing records with wrong types.")
        rows = [{**p} for p in self.b.products[:5]]
        bad_cols = ["product_sku", "product_name", "category", "price", "is_active", "updated_at"]
        for r in rows:
            r["price"] = f"{r['list_price']:.2f}"
        s.files["products/products_v003.csv"] = products_csv(rows, bad_cols)
        s.files["payments/payments_2026-11-05_truncated.json"] = payments_json(
            "payments_2026-11-05_truncated", [{"transaction_id": "PAY-S09-0001", "order_id": 1003}],
            riyadh_to_utc(datetime(2026, 11, 5, 2, 0)))[:120]
        s.files["payments/payments_2026-11-05_shape.json"] = json.dumps(
            {"schema_version": "1.0", "batch_id": "payments_2026-11-05_shape", "transactions": {"oops": True}}) + "\n"
        some_order = sorted(self.base.orders)[50]
        good = {"transaction_id": "PAY-S09-0002", "order_id": some_order, "transaction_type": "payment",
                "status": "failed", "amount": "15.00", "currency": CURRENCY, "payment_method": "mada",
                "transaction_at": "2026-11-05T08:00:00Z", "refers_to_transaction_id": None}
        recs = [good, {**good, "transaction_id": "PAY-S09-0003", "amount": True},
                {**good, "transaction_id": "PAY-S09-0004", "order_id": "abc"}, "garbage-record"]
        s.files["payments/payments_2026-11-05_types.json"] = payments_json(
            "payments_2026-11-05_types", recs, riyadh_to_utc(datetime(2026, 11, 5, 2, 0)))  # type: ignore[arg-type]
        after = self.base.copy().upsert(txns=[good])
        s.expected = {
            "warehouse_delta": delta(self.base.summary(), after.summary()),
            "files": {"rejected": 3},
            "quarantine_open": {"SCHEMA_INVALID_HEADER": 1, "SCHEMA_INVALID_JSON": 1, "SCHEMA_INVALID_STRUCTURE": 1,
                                "INVALID_TYPE": 2, "SCHEMA_INVALID_RECORD": 1},
        }
        return s

    # ------------------------------------------------------------------ s10
    def s10(self) -> Scenario:
        s = Scenario("s10_corrected_input", "Corrected input and reprocessing",
                     "A corrected products_v003.csv (same name as the rejected file, valid header) adds the six "
                     "missing SKUs. An incremental run loads it; a reprocess run re-evaluates open "
                     "MISSING_PRODUCT_REF quarantine records, which are then resolved.",
                     depends_on=["s06_catalog_price_update", "s07_invalid_product_refs", "s09_schema_invalid"],
                     steps=[{"action": "run", "mode": "incremental"},
                            {"action": "run", "mode": "reprocess",
                             "params": {"scope": "quarantine", "error_codes": ["MISSING_PRODUCT_REF"]}}])
        at = riyadh_to_utc(datetime(2026, 11, 5, 12, 0))
        bad_items = [i for i in self.s07_items if i["product_sku"].startswith("SKU-9")]
        new_products = [{"product_sku": i["product_sku"], "product_name": f"Tilal Essential New Arrival {k + 1}",
                         "category": "Home & Kitchen", "list_price": i["unit_price"], "currency": CURRENCY,
                         "is_active": True, "updated_at": at} for k, i in enumerate(bad_items)]
        s.files["products/products_v003.csv"] = products_csv(self.catalog_v2 + new_products)
        before = self.base.copy().upsert(**self.s07_accepted)
        after = before.copy().upsert(products=new_products, items=bad_items)
        s.expected = {
            "warehouse_delta": delta(before.summary(), after.summary()),
            "quarantine_resolved": {"MISSING_PRODUCT_REF": 6},
            "quarantine_open_after": {"MISSING_PRODUCT_REF": 0},
            "note": "Delta is relative to the state after s06, s07 and s09.",
        }
        return s

    # ------------------------------------------------------------------ s11
    def s11(self) -> Scenario:
        rng = self.rng(11)
        s = Scenario("s11_interruption_retry", "Pipeline interruption and retry",
                     "50 new orders and their payments. The first run is deliberately failed just before the "
                     "publish commit; the warehouse and watermark must stay unchanged. A retry then loads "
                     "everything exactly once.",
                     steps=[{"action": "run", "mode": "incremental", "params": {"fail_at": "before_commit"},
                             "expect_status": "failed"},
                            {"action": "retry_last_failed"}])
        clock = riyadh_to_utc(datetime(2026, 11, 6, 8, 0))
        orders, items, txns = [], [], []
        item_id = 1_004_001
        for k in range(50):
            oid = self.max_order + 401 + k
            placed = clock + timedelta(minutes=7 * k)
            o, its = self.new_order(rng, oid, item_id, rng.choice(sorted(self.base.customers)), placed)
            item_id += len(its)
            orders.append(o)
            items += its
            txns.append(Txn(oid, "payment", "succeeded", self.order_net(its), "apple_pay",
                            placed + timedelta(minutes=1), transaction_id=f"PAY-S11-{k + 1:04d}").to_json())
        s.ops_upserts = {"customers": [], "orders": orders, "order_items": items}
        s.files["payments/payments_2026-11-06_s11.json"] = payments_json("payments_2026-11-06_s11", txns, clock + timedelta(days=1))
        after = self.base.copy().upsert(orders=orders, items=items, txns=txns)
        s.expected = {"warehouse_delta": delta(self.base.summary(), after.summary()), "quarantine_open": {},
                      "after_failed_run": {"warehouse_delta_orders": 0, "watermark_advanced": False}}
        return s


def baseline_payment_files(b: Baseline) -> dict[str, str]:
    by_day: dict[str, list[dict]] = {}
    for t in b.files_txns:
        day = (t.at + timedelta(hours=3)).date().isoformat()  # Asia/Riyadh is UTC+3 (no DST)
        by_day.setdefault(day, []).append(t.to_json())
    out = {}
    for day, ts in sorted(by_day.items()):
        gen = riyadh_to_utc(datetime.fromisoformat(day) + timedelta(days=1, hours=2))
        out[f"payments_{day}.json"] = payments_json(f"payments_{day}", ts, gen)
    return out


def build_scenarios(b: Baseline) -> list[Scenario]:
    bl = _Builder(b)
    return [bl.s01(), bl.s02(), bl.s03(), bl.s04(), bl.s05(), bl.s06(), bl.s07(), bl.s08(), bl.s09(), bl.s10(), bl.s11()]
