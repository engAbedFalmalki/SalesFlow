"""Export the warehouse to static JSON for the hosted, server-less demo (Netlify).

    python backend/scripts/export_static.py [out_dir]     # default: frontend/public/static-data

Two kinds of output:
- snapshots: exact responses of the read-only API for endpoints without free-form filters
  (health, filters, freshness, definitions, runs, quality, quarantine, reconciliation);
- datasets: compact arrays (orders, customers, products, sales lines, payments) from which the
  browser computes the date-range endpoints (overview, timeseries, top products, orders, exports).

Money is exported as integer halalas (cents) so the browser can sum exactly.
"""

from __future__ import annotations

import json
import os
import re
import socket
import sys
from decimal import Decimal
from pathlib import Path

os.environ.update(SALESFLOW_ENV="production", SALESFLOW_READ_ONLY="true", SALESFLOW_EMBEDDED_WORKER="false")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from salesflow.api.app import app  # noqa: E402
from salesflow.db import get_engine  # noqa: E402
from salesflow.timeutil import iso_utc  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


def cents(v: Decimal | None) -> int | None:
    return None if v is None else int((Decimal(v) * 100).to_integral_value())


def write(out: Path, name: str, data) -> int:
    path = out / f"{name}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return path.stat().st_size


def scrub(data, host: str = socket.gethostname()):
    """Remove this machine's name before publishing: worker ids such as "embedded:MY-PC:1234:5678"
    become "embedded:worker", and the name is masked anywhere else it appears."""
    if isinstance(data, dict):
        return {k: (f"{v.split(':', 1)[0]}:worker" if k == "worker_id" and isinstance(v, str) and ":" in v
                    else scrub(v, host)) for k, v in data.items()}
    if isinstance(data, list):
        return [scrub(v, host) for v in data]
    if isinstance(data, str) and host and host.lower() in data.lower():
        return re.sub(re.escape(host), "worker", data, flags=re.IGNORECASE)
    return data


def snapshots(client: TestClient) -> dict:
    def get(path: str, **params):
        r = client.get(path, params=params)
        r.raise_for_status()
        return r.json()

    runs = []
    page = 1
    while True:
        p = get("/api/pipeline/runs", page=page, page_size=100)
        runs += p["items"]
        if len(runs) >= p["total"]:
            break
        page += 1
    quarantine = get("/api/quality/quarantine", page=1, page_size=100)
    q_items = quarantine["items"]
    while len(q_items) < quarantine["total"]:
        q_items += get("/api/quality/quarantine", page=len(q_items) // 100 + 1, page_size=100)["items"]
    recon: dict[str, dict] = {"latest": get("/api/reconciliation")}
    for r in runs:
        d = get("/api/reconciliation", run_id=r["run_id"])
        if d.get("results"):
            recon[str(r["run_id"])] = d
    return {
        "health": get("/api/health"),
        "filters": get("/api/filters"),
        "freshness": get("/api/freshness"),
        "definitions": {"en": get("/api/definitions", lang="en"), "ar": get("/api/definitions", lang="ar")},
        "runs": runs,
        "run_details": {str(r["run_id"]): get(f"/api/pipeline/runs/{r['run_id']}") for r in runs},
        "quality": get("/api/quality"),
        "quarantine": {"items": q_items, "error_codes": quarantine["error_codes"],
                       "details": {str(i["quarantine_id"]): get(f"/api/quality/quarantine/{i['quarantine_id']}")
                                   for i in q_items}},
        "reconciliation": recon,
    }


def datasets() -> dict:
    with get_engine("dw").connect() as c:
        rows = lambda sql: [tuple(r) for r in c.execute(text(sql))]  # noqa: E731
        customers = rows("""SELECT CustomerKey, CustomerCode, FullName, City, Region FROM dw.DimCustomer ORDER BY CustomerKey""")
        products = rows("""SELECT ProductKey, ProductSku, ProductName, Category, CurrentListPrice FROM dw.DimProduct
                           ORDER BY ProductKey""")
        orders = rows("""SELECT o.OrderKey, o.OrderId, o.OrderPlacedAtUtc, d.[Date], o.OrderStatus, o.Channel, o.CustomerKey,
                                o.UpdatedRunId
                         FROM dw.DimOrder o JOIN dw.DimDate d ON d.DateKey = o.OrderDateKey ORDER BY o.OrderKey""")
        lines = rows("""SELECT f.OrderKey, f.OrderItemId, f.LineNumber, f.ProductKey, f.Quantity, f.UnitPrice,
                               f.DiscountAmount, f.GrossAmount, f.NetAmount, f.OrderDateKey
                        FROM dw.FactSalesLine f ORDER BY f.OrderKey, f.LineNumber""")
        txns = rows("""SELECT t.OrderKey, t.TransactionId, t.TransactionType, t.TransactionStatus, t.Amount, t.PaymentMethod,
                              t.TransactionAtUtc, d.[Date], t.RefersToTransactionId, f.file_name
                       FROM dw.FactPaymentTransaction t JOIN dw.DimDate d ON d.DateKey = t.TransactionDateKey
                       LEFT JOIN meta.source_file f ON f.file_id = t.SourceFileId
                       ORDER BY t.OrderKey, t.TransactionAtUtc, t.TransactionId""")
        # Open or ignored quarantine records that belong to an order (shown in the order drawer).
        oq = rows("""SELECT TRY_CAST(COALESCE(CASE WHEN entity = 'order' THEN source_record_id END,
                                              CASE WHEN ISJSON(payload) = 1 THEN JSON_VALUE(payload, '$.order_id') END) AS INT),
                            quarantine_id, entity, source_record_id, error_code, error_reason, error_reason_ar, status
                     FROM qa.quarantine_record
                     WHERE status IN ('open','ignored') AND entity IN ('order','order_item','payment_txn')""")
    cust_idx = {k: i for i, (k, *_r) in enumerate(customers)}
    prod_idx = {k: i for i, (k, *_r) in enumerate(products)}
    order_idx = {k: i for i, (k, *_r) in enumerate(orders)}
    order_quarantine: dict[str, list] = {}
    for oid, *rest in oq:
        if oid is not None:
            order_quarantine.setdefault(str(oid), []).append(
                dict(zip(["quarantine_id", "entity", "source_record_id", "error_code", "error_reason", "error_reason_ar",
                          "status"], rest)))
    return {
        "customers": [[code, name, city, region] for _k, code, name, city, region in customers],
        "products": [[sku, name, cat, cents(price)] for _k, sku, name, cat, price in products],
        # order_id, placed_at_utc, order_date, status, channel, customer index, updated run
        "orders": [[oid, iso_utc(placed), d.isoformat(), st, ch, cust_idx[ck], run]
                   for _k, oid, placed, d, st, ch, ck, run in orders],
        # order index, item id, line number, product index, quantity, unit price, discount, gross, net (cents)
        "lines": [[order_idx[ok], item, ln, prod_idx[pk], qty, cents(up), cents(disc), cents(gross), cents(net)]
                  for ok, item, ln, pk, qty, up, disc, gross, net, _dk in lines],
        # order index, transaction id, type, status, amount (cents), method, at_utc, date, refers to, file
        "txns": [[order_idx[ok], tid, typ, st, cents(amt), m, iso_utc(at), d.isoformat(), ref, fname]
                 for ok, tid, typ, st, amt, m, at, d, ref, fname in txns],
        "order_quarantine": order_quarantine,
    }


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "frontend" / "public" / "static-data"
    out.mkdir(parents=True, exist_ok=True)
    with TestClient(app) as client:
        snap = snapshots(client)
    sizes = {"snapshots": write(out, "snapshots", scrub(snap)), "data": write(out, "data", scrub(datasets()))}
    print(json.dumps({"out": str(out), "bytes": sizes}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
