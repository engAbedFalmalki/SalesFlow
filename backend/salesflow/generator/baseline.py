"""Deterministic synthetic baseline: customers, catalog, orders, payments.

Everything is driven by one ``random.Random(seed)`` so the same configuration
always produces byte-identical output. All people, emails and brands are
fictional. Money uses ``Decimal`` exclusively.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from .. import geo
from ..timeutil import riyadh_to_utc

CENT = Decimal("0.01")
CURRENCY = "SAR"
# A fixed catalog effective time keeps output independent of wall-clock time.
CATALOG_V1_AT = datetime(2026, 7, 31, 21, 0, 0)  # 2026-08-01 00:00 Riyadh, as UTC


@dataclass(frozen=True)
class GenConfig:
    seed: int = 20261101
    customers: int = 2000
    products: int = 500
    orders: int = 10000
    start_date: date = date(2026, 8, 1)  # inclusive, Asia/Riyadh business dates
    end_date: date = date(2026, 10, 31)  # inclusive
    late_cod_payments: int = 20          # COD collections withheld for the late-arrival scenario

    @property
    def as_of_utc(self) -> datetime:
        """End of the simulated period (exclusive), as naive UTC."""
        return riyadh_to_utc(datetime.combine(self.end_date + timedelta(days=1), datetime.min.time()))

    def to_dict(self) -> dict:
        return {
            "seed": self.seed,
            "customers": self.customers,
            "products": self.products,
            "orders": self.orders,
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "late_cod_payments": self.late_cod_payments,
        }


# (category, share of catalog, demand weight, (min price, max price), nouns)
CATEGORIES: list[tuple[str, int, float, tuple[int, int], list[str]]] = [
    ("Electronics", 60, 1.1, (149, 3999), ["Wireless Earbuds", "Smart Watch", "Bluetooth Speaker", "Power Bank", "USB-C Hub", "Gaming Mouse", "Mechanical Keyboard", "Action Camera", "Tablet Stand", "Portable SSD"]),
    ("Home & Kitchen", 90, 1.0, (25, 899), ["Coffee Maker", "Air Fryer", "Knife Set", "Cookware Set", "Blender", "Bedsheet Set", "Table Lamp", "Storage Box", "Water Kettle", "Dinner Set"]),
    ("Fashion", 100, 1.4, (49, 699), ["Cotton Thobe", "Abaya", "Sneakers", "Leather Wallet", "Sunglasses", "Shemagh", "Hoodie", "Denim Jacket", "Sandals", "Backpack"]),
    ("Beauty & Personal Care", 70, 1.3, (19, 449), ["Oud Perfume", "Face Serum", "Hair Dryer", "Moisturizer", "Bakhoor Set", "Shampoo", "Electric Shaver", "Lip Care Set", "Sunscreen", "Body Mist"]),
    ("Grocery & Gourmet", 80, 1.9, (6, 149), ["Arabic Coffee", "Sukkari Dates", "Saffron", "Honey Jar", "Basmati Rice", "Olive Oil", "Mixed Nuts", "Green Tea", "Date Syrup", "Spice Mix"]),
    ("Sports & Outdoors", 50, 0.7, (39, 1499), ["Yoga Mat", "Dumbbell Set", "Camping Tent", "Running Shoes", "Water Bottle", "Cycling Helmet", "Resistance Bands", "Football", "Hiking Bag", "Fitness Tracker"]),
    ("Toys & Games", 30, 0.6, (25, 499), ["Building Blocks", "Board Game", "Jigsaw Puzzle", "RC Car", "Plush Camel", "Art Kit", "Science Kit", "Card Game", "Doll House", "Mini Drone"]),
    ("Books & Stationery", 20, 0.5, (9, 199), ["Notebook Set", "Fountain Pen", "Weekly Planner", "Calligraphy Set", "Paperback Novel", "Sketchbook", "Desk Organizer", "Colored Pencils", "Cookbook", "Journal"]),
]
BRANDS = ["Nakheel", "Sahra", "Darb", "Wadi", "Najm", "Rimal", "Qamar", "Faris", "Bayt", "Sawt", "Noorah", "Tilal"]
ADJECTIVES = ["Classic", "Premium", "Essential", "Pro", "Lite", "Deluxe", "Everyday", "Signature", "Compact", "Ultra", "Eco", "Royal"]
FIRST_NAMES = ["Aisha", "Omar", "Lina", "Faisal", "Noura", "Khalid", "Sara", "Yousef", "Huda", "Majed", "Reem", "Tariq",
               "Dana", "Saleh", "Maha", "Nasser", "Hessa", "Badr", "Jana", "Rakan", "Lulwa", "Turki", "Abeer", "Ziyad",
               "Amal", "Hamad", "Razan", "Fahad", "Wafa", "Sultan", "Ghada", "Bandar", "Leen", "Mazen", "Shahad",
               "Nawaf", "Rana", "Adel", "Malak", "Hani"]
SURNAME_ROOTS = ["Qa", "Ra", "Su", "Ha", "Ma", "Ta", "Na", "Za", "Ba", "Fa", "Da", "Ka", "Ja", "Sa", "Wa"]
SURNAME_ENDS = ["deri", "lami", "rini", "wani", "bari", "hani", "sari", "firi", "mani", "lowi", "tami", "zuri"]
CITIES = [(city, weight) for city, _ar, _region, weight in geo.CITIES]  # cities in all 13 regions
DOW_FACTOR = {1: 0.9, 2: 0.9, 3: 0.95, 4: 1.15, 5: 1.3, 6: 1.25, 7: 0.95}  # ISO weekday, Fri/Sat busiest
HOUR_WEIGHTS = [0.5, 0.3, 0.2, 0.1, 0.1, 0.1, 0.2, 0.4, 0.6, 0.8, 1.0, 1.1, 1.1, 1.0, 1.0, 1.0, 1.1, 1.2, 1.3, 1.5,
                1.7, 1.8, 1.6, 1.0]
PAYMENT_METHODS = [("mada", 38), ("credit_card", 24), ("apple_pay", 20), ("stc_pay", 6), ("cash_on_delivery", 12)]


def money(x: Decimal | int | float | str) -> Decimal:
    return Decimal(str(x)).quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass
class Txn:
    order_id: int
    transaction_type: str          # payment | refund
    status: str                    # succeeded | failed | pending
    amount: Decimal                # always positive
    method: str
    at: datetime                   # naive UTC
    refers_to: "Txn | None" = None
    transaction_id: str = ""
    late: bool = False             # withheld from baseline files (late-arrival scenario)

    def to_json(self) -> dict:
        return {
            "transaction_id": self.transaction_id,
            "order_id": self.order_id,
            "transaction_type": self.transaction_type,
            "status": self.status,
            "amount": f"{self.amount:.2f}",
            "currency": CURRENCY,
            "payment_method": self.method,
            "transaction_at": self.at.isoformat(timespec="seconds") + "Z",
            "refers_to_transaction_id": self.refers_to.transaction_id if self.refers_to else None,
        }


@dataclass
class Baseline:
    config: GenConfig
    customers: list[dict] = field(default_factory=list)
    products: list[dict] = field(default_factory=list)
    orders: list[dict] = field(default_factory=list)
    items: list[dict] = field(default_factory=list)
    txns: list[Txn] = field(default_factory=list)
    meta: dict = field(default_factory=dict)  # per-order facts used by scenarios

    @property
    def files_txns(self) -> list[Txn]:
        return [t for t in self.txns if not t.late]


def _weighted(rng: random.Random, pairs: list[tuple[str, int]]) -> str:
    return rng.choices([p[0] for p in pairs], weights=[p[1] for p in pairs])[0]


def _price(rng: random.Random, lo: int, hi: int) -> Decimal:
    raw = lo * (hi / lo) ** (rng.random() ** 1.6)
    whole = max(lo, int(round(raw)))
    return money(whole) - (Decimal("0.05") if rng.random() < 0.5 else Decimal("0"))


def _gen_products(rng: random.Random, cfg: GenConfig) -> tuple[list[dict], list[float]]:
    total_share = sum(c[1] for c in CATEGORIES)
    counts = [max(2, round(cfg.products * c[1] / total_share)) for c in CATEGORIES]
    counts[2] += cfg.products - sum(counts)  # absorb rounding in the largest category
    products: list[dict] = []
    weights: list[float] = []
    names: set[str] = set()
    sku = 0
    for (cat, _share, demand, (lo, hi), nouns), n in zip(CATEGORIES, counts):
        zipf = [1 / (r + 1) ** 0.9 for r in range(n)]
        zsum = sum(zipf)
        for r in range(n):
            sku += 1
            name = f"{rng.choice(BRANDS)} {rng.choice(ADJECTIVES)} {rng.choice(nouns)}"
            while name in names:
                name += " II"
            names.add(name)
            products.append({
                "product_sku": f"SKU-{sku:05d}",
                "product_name": name,
                "category": cat,
                "list_price": _price(rng, lo, hi),
                "currency": CURRENCY,
                "is_active": rng.random() > 0.03,
                "updated_at": CATALOG_V1_AT,
            })
            weights.append(demand * zipf[r] / zsum)
    # Fixed products for the documented Order 1001 example.
    products[0].update(product_sku="SKU-00001", product_name="Bayt Classic Water Kettle",
                       category="Home & Kitchen", list_price=money(100), is_active=True)
    products[1].update(product_sku="SKU-00002", product_name="Sawt Pro Bluetooth Speaker",
                       category="Electronics", list_price=money(300), is_active=True)
    return products, weights


def _gen_order_times(rng: random.Random, cfg: GenConfig, n: int) -> list[datetime]:
    days = [(cfg.start_date + timedelta(days=i)) for i in range((cfg.end_date - cfg.start_date).days + 1)]
    dw = []
    for i, d in enumerate(days):
        w = DOW_FACTOR[d.isoweekday()] * (1 + 0.15 * i / max(1, len(days) - 1))
        if d.day in (27, 28, 29):  # salary-day uplift
            w *= 1.2
        dw.append(w)
    chosen = rng.choices(days, weights=dw, k=n)
    out = []
    for d in chosen:
        hour = rng.choices(range(24), weights=HOUR_WEIGHTS)[0]
        local = datetime(d.year, d.month, d.day, hour, rng.randrange(60), rng.randrange(60))
        out.append(riyadh_to_utc(local))
    out.sort()
    return out


def generate_baseline(cfg: GenConfig) -> Baseline:
    rng = random.Random(cfg.seed)
    b = Baseline(config=cfg)
    as_of = cfg.as_of_utc
    period_start_utc = riyadh_to_utc(datetime.combine(cfg.start_date, datetime.min.time()))

    products, pweights = _gen_products(rng, cfg)
    b.products = products
    pcum: list[float] = []
    acc = 0.0
    for w in pweights:
        acc += w
        pcum.append(acc)

    # Customers: heavy-tailed propensity gives realistic repeat buying.
    cust_weight = [rng.paretovariate(1.2) for _ in range(cfg.customers)]
    cust_weight[0] = max(cust_weight[0], 1.0)

    # Orders: 1001 and 1002 are fixed examples; the rest are random.
    times = _gen_order_times(rng, cfg, cfg.orders - 2)
    fixtures = [
        (1001, riyadh_to_utc(datetime(2026, 8, 1, 10, 15, 0)), 1),
        # Placed 2026-08-31 22:30 UTC = 2026-09-01 01:30 Riyadh (timezone boundary example).
        (1002, datetime(2026, 8, 31, 22, 30, 0), 2),
    ]
    order_rows: list[tuple[int, datetime, int]] = list(fixtures)
    cust_ids = list(range(1, cfg.customers + 1))
    for i, t in enumerate(times):
        cid = rng.choices(cust_ids, weights=cust_weight)[0]
        order_rows.append((1003 + i, t, cid))

    first_order: dict[int, datetime] = {}
    for oid, t, cid in order_rows:
        if cid not in first_order or t < first_order[cid]:
            first_order[cid] = t

    # Customer master data.
    for cid in cust_ids:
        first = rng.choice(FIRST_NAMES)
        last = f"Al-{rng.choice(SURNAME_ROOTS)}{rng.choice(SURNAME_ENDS)}"
        lo = datetime(2025, 1, 1)
        if cid not in first_order:  # registered, never ordered
            created = lo + timedelta(seconds=rng.randrange(int((as_of - lo).total_seconds()) - 3600))
        elif rng.random() < 0.6:   # existing customer from before the period
            hi = min(first_order[cid], period_start_utc)
            created = lo + timedelta(seconds=rng.randrange(max(1, int((hi - lo).total_seconds()) - 60)))
        else:                       # signed up shortly before the first order
            created = first_order[cid] - timedelta(minutes=rng.randrange(10, 4320))
        updated = created
        if rng.random() < 0.08:
            span = int((as_of - created).total_seconds())
            if span > 3600:
                updated = created + timedelta(seconds=rng.randrange(3600, span))
        b.customers.append({
            "customer_id": cid,
            "customer_code": f"C{cid:05d}",
            "first_name": first,
            "last_name": last,
            "email": f"{first}.{last.replace('Al-', 'al')}{cid}@example.com".lower(),
            "city": _weighted(rng, CITIES),
            "created_at": created,
            "updated_at": updated,
        })
    b.customers[0].update(first_name="Aisha", last_name="Al-Qaderi", email="aisha.alqaderi1@example.com")

    item_id = 0
    for oid, placed, cid in order_rows:
        # ---- order lines
        if oid == 1001:
            lines = [(products[0], 2, Decimal("0")), (products[1], 1, Decimal("0"))]
        elif oid == 1002:
            lines = [(products[1], 1, Decimal("0"))]
        else:
            n_lines = rng.choices([1, 2, 3, 4, 5], weights=[45, 28, 15, 8, 4])[0]
            chosen: list[dict] = []
            while len(chosen) < n_lines:
                p = rng.choices(products, cum_weights=pcum)[0]
                if p not in chosen and p["is_active"]:
                    chosen.append(p)
            lines = []
            for p in chosen:
                if p["category"] == "Grocery & Gourmet":
                    qty = rng.choices([1, 2, 3, 4, 6], weights=[50, 25, 12, 8, 5])[0]
                else:
                    qty = rng.choices([1, 2, 3], weights=[80, 16, 4])[0]
                disc = Decimal("0")
                if rng.random() < 0.12:
                    pct = Decimal(rng.choice([5, 10, 15, 20]))
                    disc = money(p["list_price"] * qty * pct / 100)
                lines.append((p, qty, disc))

        # ---- lifecycle and status as of the end of the simulated period
        method = "mada" if oid in (1001, 1002) else _weighted(rng, PAYMENT_METHODS)
        shipped_at = placed + timedelta(hours=rng.uniform(24, 60))
        delivered_at = shipped_at + timedelta(hours=rng.uniform(30, 110))
        cancelled_at = placed + timedelta(hours=rng.uniform(0.5, 48))
        r = rng.random()
        if oid in (1001, 1002):
            status = "delivered"
            shipped_at, delivered_at = placed + timedelta(days=1), placed + timedelta(days=3)
        elif r < 0.06 and cancelled_at < as_of:
            status = "cancelled"
        elif r < 0.08 and (as_of - placed) < timedelta(days=4):
            status = "pending"
        elif delivered_at < as_of:
            status = "delivered"
        elif shipped_at < as_of:
            status = "shipped"
        else:
            status = "processing"
        updated = {"delivered": delivered_at, "shipped": shipped_at, "cancelled": cancelled_at}.get(status, placed)

        b.orders.append({
            "order_id": oid,
            "customer_id": cid,
            "order_placed_at": placed,
            "status": status,
            "currency": CURRENCY,
            "channel": "app" if rng.random() < 0.55 else "web",
            "created_at": placed,
            "updated_at": updated,
        })
        order_net = Decimal("0")
        line_nets = []
        for ln, (p, qty, disc) in enumerate(lines, start=1):
            item_id += 1
            b.items.append({
                "order_item_id": item_id,
                "order_id": oid,
                "line_number": ln,
                "product_sku": p["product_sku"],
                "quantity": qty,
                "unit_price": p["list_price"],
                "discount_amount": disc,
                "created_at": placed,
                "updated_at": placed,
            })
            net = p["list_price"] * qty - disc
            line_nets.append((net, qty))
            order_net += net

        # ---- payments and refunds
        txns: list[Txn] = []
        if oid == 1001:
            pay = Txn(oid, "payment", "succeeded", money(500), "mada", placed + timedelta(minutes=1))
            txns += [pay, Txn(oid, "refund", "succeeded", money(100), "mada",
                              riyadh_to_utc(datetime(2026, 8, 9, 12, 0, 0)), refers_to=pay)]
        elif oid == 1002:
            txns.append(Txn(oid, "payment", "succeeded", order_net, "mada", placed + timedelta(minutes=1)))
        elif method == "cash_on_delivery":
            if status == "delivered":
                txns.append(Txn(oid, "payment", "succeeded", order_net, method, delivered_at))
        elif status == "pending":
            if rng.random() < 0.5:
                txns.append(Txn(oid, "payment", "failed", order_net, method, placed + timedelta(minutes=1)))
        elif status == "cancelled":
            if rng.random() < 0.5:
                if rng.random() < 0.3:
                    txns.append(Txn(oid, "payment", "failed", order_net, method, placed + timedelta(minutes=1)))
            else:
                pay = Txn(oid, "payment", "succeeded", order_net, method, placed + timedelta(minutes=2))
                txns.append(pay)
                ref_at = max(cancelled_at, pay.at) + timedelta(hours=rng.uniform(1, 72))
                if ref_at < as_of:
                    txns.append(Txn(oid, "refund", "succeeded", order_net, method, ref_at, refers_to=pay))
        else:
            t0 = placed + timedelta(minutes=1)
            if rng.random() < 0.07:
                alt = _weighted(rng, PAYMENT_METHODS[:4])
                txns.append(Txn(oid, "payment", "failed", order_net, alt, t0))
                t0 += timedelta(minutes=rng.randrange(2, 10))
            pay = Txn(oid, "payment", "succeeded", order_net, method, t0)
            txns.append(pay)
            multi_unit = len(line_nets) >= 2 or line_nets[0][1] >= 2
            if status == "delivered" and multi_unit and rng.random() < 0.06:
                net, qty = rng.choice(line_nets)
                refund_amt = money(net / qty)
                ref_at = delivered_at + timedelta(hours=rng.uniform(24, 240))
                if ref_at < as_of and refund_amt > 0:
                    if rng.random() < 0.1:
                        txns.append(Txn(oid, "refund", "failed", refund_amt, method, ref_at - timedelta(hours=2), refers_to=pay))
                    txns.append(Txn(oid, "refund", "succeeded", refund_amt, method, ref_at, refers_to=pay))
        txns = [t for t in txns if t.at < as_of]
        b.txns.extend(txns)
        b.meta[oid] = {"method": method, "status": status, "net": order_net, "delivered_at": delivered_at,
                       "placed": placed, "customer_id": cid, "has_txns": bool(txns)}

    # Withhold some October COD collections: they arrive later (late-arrival scenario).
    oct_start = riyadh_to_utc(datetime(cfg.end_date.year, cfg.end_date.month, 1))
    cod = sorted((t for t in b.txns if t.method == "cash_on_delivery" and t.status == "succeeded"
                  and t.at >= oct_start), key=lambda t: t.order_id)
    for t in rng.sample(cod, min(cfg.late_cod_payments, len(cod))):
        t.late = True

    # Assign transaction ids in time order.
    b.txns.sort(key=lambda t: (t.at, t.order_id, t.transaction_type))
    seq = {"payment": 0, "refund": 0}
    for t in b.txns:
        seq[t.transaction_type] += 1
        prefix = "PAY" if t.transaction_type == "payment" else "RFD"
        t.transaction_id = f"{prefix}-{seq[t.transaction_type]:07d}"
    return b
