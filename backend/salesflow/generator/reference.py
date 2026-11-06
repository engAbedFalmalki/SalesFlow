"""Independent reference implementation of the metric definitions.

Used by the generator to write expected outcomes into the scenario manifest, and
by tests as an oracle that does not share code with the SQL warehouse queries.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal

ZERO = Decimal("0.00")


@dataclass
class RefState:
    """Accepted (valid) warehouse-equivalent state keyed by business key."""

    customers: dict[int, dict] = field(default_factory=dict)
    products: dict[str, dict] = field(default_factory=dict)
    orders: dict[int, dict] = field(default_factory=dict)
    items: dict[int, dict] = field(default_factory=dict)
    txns: dict[str, dict] = field(default_factory=dict)  # Txn.to_json() shape

    def copy(self) -> "RefState":
        return RefState(dict(self.customers), dict(self.products), dict(self.orders), dict(self.items), dict(self.txns))

    def upsert(self, *, customers=(), products=(), orders=(), items=(), txns=()) -> "RefState":
        for c in customers:
            self.customers[c["customer_id"]] = c
        for p in products:
            self.products[p["product_sku"]] = p
        for o in orders:
            self.orders[o["order_id"]] = o
        for i in items:
            self.items[i["order_item_id"]] = i
        for t in txns:
            self.txns[t["transaction_id"]] = t
        return self

    def summary(self) -> dict:
        return summarize(self.orders, self.items, self.txns, len(self.customers), len(self.products))


def summarize(orders: dict[int, dict], items: dict[int, dict], txns: dict[str, dict],
              n_customers: int, n_products: int) -> dict:
    gmv = ordered = active = ZERO
    active_orders: set[int] = set()
    for it in items.values():
        gross = Decimal(it["quantity"]) * Decimal(str(it["unit_price"]))
        net = gross - Decimal(str(it["discount_amount"]))
        gmv += gross
        ordered += net
        if orders[it["order_id"]]["status"] != "cancelled":
            active += net
            active_orders.add(it["order_id"])
    coll = refunds = ZERO
    for t in txns.values():
        if t["status"] != "succeeded":
            continue
        if t["transaction_type"] == "payment":
            coll += Decimal(t["amount"])
        else:
            refunds += Decimal(t["amount"])
    statuses = Counter(o["status"] for o in orders.values())
    return {
        "customers": n_customers,
        "products": n_products,
        "orders": len(orders),
        "sales_lines": len(items),
        "payment_transactions": len(txns),
        "gross_merchandise_value": f"{gmv:.2f}",
        "ordered_value": f"{ordered:.2f}",
        "active_ordered_value": f"{active:.2f}",
        "distinct_active_orders": len(active_orders),
        "successful_collections": f"{coll:.2f}",
        "successful_refunds": f"{refunds:.2f}",
        "net_collections": f"{coll - refunds:.2f}",
        "order_status_counts": dict(sorted(statuses.items())),
    }


ADDITIVE_INT = ("customers", "products", "orders", "sales_lines", "payment_transactions", "distinct_active_orders")
ADDITIVE_MONEY = ("gross_merchandise_value", "ordered_value", "active_ordered_value",
                  "successful_collections", "successful_refunds", "net_collections")


def delta(before: dict, after: dict) -> dict:
    out: dict = {k: after[k] - before[k] for k in ADDITIVE_INT}
    for k in ADDITIVE_MONEY:
        out[k] = f"{Decimal(after[k]) - Decimal(before[k]):.2f}"
    statuses = set(before["order_status_counts"]) | set(after["order_status_counts"])
    out["order_status_counts"] = {
        s: after["order_status_counts"].get(s, 0) - before["order_status_counts"].get(s, 0) for s in sorted(statuses)
    }
    return out
