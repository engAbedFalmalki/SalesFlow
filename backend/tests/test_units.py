"""Database-free unit tests: validation rules, payload canonicalisation,
generator determinism, reset guards."""

from __future__ import annotations

import hashlib
from decimal import Decimal
from pathlib import Path

import pytest

from salesflow import geo
from salesflow.config import Settings
from salesflow.devtools import ResetRefused, assert_reset_allowed
from salesflow.generator.baseline import GenConfig, generate_baseline
from salesflow.generator.reference import RefState
from salesflow.generator.scenarios import baseline_state, build_scenarios
from salesflow.pipeline.common import canonical_json, load_payload
from salesflow.pipeline.files import FileRejected, parse_payments, parse_products
from salesflow.pipeline.validate import parse_order, parse_order_item, parse_payment

OK_ITEM = {"order_item_id": 1, "order_id": 5, "line_number": 1, "product_sku": "sku-1", "quantity": 2,
           "unit_price": Decimal("10.00"), "discount_amount": Decimal("0.00"),
           "created_at": "2026-08-01T00:00:00.000Z", "updated_at": "2026-08-01T00:00:00.000Z"}
OK_TXN = {"transaction_id": "P1", "order_id": 5, "transaction_type": "payment", "status": "succeeded",
          "amount": "10.00", "currency": "SAR", "payment_method": "mada", "transaction_at": "2026-08-01T00:00:00Z"}


def codes(issues) -> list[str]:
    return [i.code for i in issues]


@pytest.mark.parametrize("change,code", [
    ({"quantity": 0}, "INVALID_QUANTITY"), ({"quantity": -1}, "INVALID_QUANTITY"), ({"quantity": 1.5}, "INVALID_TYPE"),
    ({"unit_price": Decimal("-1")}, "NEGATIVE_AMOUNT"), ({"unit_price": Decimal("1.234")}, "INVALID_PRECISION"),
    ({"discount_amount": Decimal("25.00")}, "INVALID_DISCOUNT"), ({"product_sku": None}, "REQUIRED_FIELD"),
    ({"updated_at": "2026-07-01T00:00:00Z"}, "IMPOSSIBLE_TIMESTAMP"), ({"created_at": "yesterday"}, "INVALID_TYPE"),
])
def test_order_item_rules(change, code):
    rec, issues = parse_order_item({**OK_ITEM, **change})
    assert rec is None and code in codes(issues)


def test_order_item_normalisation():
    rec, issues = parse_order_item(OK_ITEM)
    assert issues == [] and rec["product_sku"] == "SKU-1" and rec["unit_price"] == Decimal("10.00")


@pytest.mark.parametrize("change,code", [
    ({"amount": "-5.00"}, "NEGATIVE_AMOUNT"), ({"amount": "0"}, "INVALID_AMOUNT"), ({"amount": True}, "INVALID_TYPE"),
    ({"amount": "1.005"}, "INVALID_PRECISION"), ({"currency": "usd"}, "UNSUPPORTED_CURRENCY"),
    ({"status": "maybe"}, "INVALID_STATUS"), ({"transaction_type": "chargeback"}, "INVALID_TRANSACTION_TYPE"),
    ({"order_id": "abc"}, "INVALID_TYPE"), ({"order_id": None}, "REQUIRED_FIELD"),
    ({"transaction_type": "refund"}, "INVALID_REFUND_REF"), ({"transaction_at": "2031-01-01T00:00:00Z"}, "DATE_OUT_OF_RANGE"),
])
def test_payment_rules(change, code):
    rec, issues = parse_payment({**OK_TXN, **change})
    assert rec is None and code in codes(issues)


def test_payment_normalisation_and_timezones():
    rec, issues = parse_payment({**OK_TXN, "status": "SUCCESS", "transaction_type": "Charge",
                                 "transaction_at": "2026-08-01T03:00:00+03:00", "amount": Decimal("10.5")})
    assert issues == []
    assert rec["transaction_status"] == "succeeded" and rec["transaction_type"] == "payment"
    assert rec["transaction_at_utc"].isoformat() == "2026-08-01T00:00:00" and rec["amount"] == Decimal("10.50")
    assert parse_payment("garbage")[1][0].code == "SCHEMA_INVALID_RECORD"


def test_order_status_synonyms_and_invalid():
    base = {"order_id": 1, "customer_id": 1, "order_placed_at": "2026-08-01T00:00:00Z", "status": "Canceled",
            "currency": "sar", "created_at": "2026-08-01T00:00:00Z", "updated_at": "2026-08-01T00:00:00Z"}
    rec, issues = parse_order(base)
    assert issues == [] and rec["order_status"] == "cancelled" and rec["currency"] == "SAR"
    assert codes(parse_order({**base, "status": "teleported"})[1]) == ["INVALID_STATUS"]


def test_canonical_json_is_exact_and_stable():
    a = canonical_json({"b": Decimal("10.50"), "a": [1, None, True], "c": "ü"})
    assert a == '{"a":[1,null,true],"b":10.50,"c":"ü"}'
    assert canonical_json(load_payload(a)) == a  # round trip keeps the decimal literal
    assert load_payload('{"x": 0.1}')["x"] == Decimal("0.1")


def test_file_contracts():
    with pytest.raises(FileRejected) as e:
        parse_products("products_v001.csv", b"product_sku,price\nSKU-1,1\n")
    assert e.value.code == "SCHEMA_INVALID_HEADER" and "list_price" in e.value.reason
    with pytest.raises(FileRejected) as e:
        parse_products("catalog.csv", b"")
    assert e.value.code == "SCHEMA_INVALID_FILENAME"
    for data, code in ((b'{"schema_version": "1.0", "transactions": [', "SCHEMA_INVALID_JSON"),
                       (b'{"schema_version": "2.0", "transactions": []}', "SCHEMA_INVALID_STRUCTURE"),
                       (b'{"schema_version": "1.0", "transactions": {}}', "SCHEMA_INVALID_STRUCTURE"),
                       (b'[1, 2]', "SCHEMA_INVALID_STRUCTURE")):
        with pytest.raises(FileRejected) as e:
            parse_payments("p.json", data)
        assert e.value.code == code
    recs = parse_payments("p.json", b'{"schema_version": "1.0", "transactions": [{"transaction_id": " T1 "}, 5]}')
    assert [r[0] for r in recs] == ["T1", "p.json#1"]


def test_generator_is_deterministic(tmp_path: Path):
    from salesflow.generator.io import write_dataset

    def digest(d: Path) -> str:
        h = hashlib.sha256()
        for f in sorted(p for p in d.rglob("*") if p.is_file()):
            h.update(str(f.relative_to(d)).encode())
            h.update(f.read_bytes())
        return h.hexdigest()
    cfg = GenConfig(seed=11, customers=80, products=30, orders=300)
    s1, s2 = Settings(data_dir=tmp_path / "a"), Settings(data_dir=tmp_path / "b")
    write_dataset(cfg, s1)
    write_dataset(cfg, s2)
    assert digest(tmp_path / "a") == digest(tmp_path / "b")
    s3 = Settings(data_dir=tmp_path / "c")
    write_dataset(GenConfig(seed=12, customers=80, products=30, orders=300), s3)
    assert digest(tmp_path / "a") != digest(tmp_path / "c")


def test_generated_data_has_required_patterns():
    b = generate_baseline(GenConfig())
    assert len(b.orders) == 10000 and len(b.customers) == 2000 and len(b.products) == 500
    assert {o["currency"] for o in b.orders} == {"SAR"}
    per_customer: dict[int, int] = {}
    for o in b.orders:
        per_customer[o["customer_id"]] = per_customer.get(o["customer_id"], 0) + 1
    assert sum(1 for n in per_customer.values() if n > 1) > 500          # repeat customers
    lines: dict[int, int] = {}
    for i in b.items:
        lines[i["order_id"]] = lines.get(i["order_id"], 0) + 1
    assert sum(1 for n in lines.values() if n > 1) > 3000                # multi-line orders
    assert sum(1 for o in b.orders if o["status"] == "cancelled") > 300
    txn_per_order: dict[int, list] = {}
    for t in b.txns:
        txn_per_order.setdefault(t.order_id, []).append(t)
    assert any([x.status for x in ts if x.transaction_type == "payment"][:2] == ["failed", "succeeded"]
               for ts in txn_per_order.values())                       # failed then successful payment
    partial = [t for t in b.txns if t.transaction_type == "refund" and t.status == "succeeded"
               and t.refers_to and t.amount < t.refers_to.amount]
    assert len(partial) > 50                                            # partial refunds
    by_dow: dict[int, int] = {}
    from salesflow.timeutil import business_date
    for o in b.orders:
        d = business_date(o["order_placed_at"]).isoweekday()
        by_dow[d] = by_dow.get(d, 0) + 1
    assert by_dow[5] > by_dow[1] * 1.2                                  # Friday busier than Monday
    s1001 = RefState(**{k: v for k, v in vars(baseline_state(b)).items()}).summary()
    assert s1001["orders"] == 10000
    o1001 = [i for i in b.items if i["order_id"] == 1001]
    assert sorted((i["quantity"], str(i["unit_price"])) for i in o1001) == [(1, "300.00"), (2, "100.00")]
    assert len(build_scenarios(b)) == 11
    regions = {geo.region_of(c["city"]) for c in b.customers}
    assert None not in regions and len(regions) == 13                  # customers in every region


def test_geography_reference():
    assert len(geo.REGIONS) == 13
    assert len({c[0].casefold() for c in geo.CITIES}) == len(geo.CITIES)  # city names are unique keys
    assert all(c[2] in geo.REGIONS and c[3] > 0 for c in geo.CITIES)
    assert geo.region_of("khobar") == "Eastern Province" and geo.region_of(" Abha ") == "Asir"
    assert geo.region_of("Atlantis") is None and geo.region_of(None) is None
    assert geo.label("Eastern Province", "ar") == "المنطقة الشرقية" and geo.label("Jeddah", "ar") == "جدة"
    assert geo.label("Jeddah", "en") == "Jeddah" and geo.label("Atlantis", "ar") == "Atlantis"


def test_reset_guards():
    with pytest.raises(ResetRefused):
        assert_reset_allowed(Settings(env="production"), "SalesFlow_DW")
    with pytest.raises(ResetRefused):
        assert_reset_allowed(Settings(env="development", dw_db="Payroll"), "Payroll")
    with pytest.raises(ResetRefused):
        assert_reset_allowed(Settings(env="development", sql_server="prod-db.example.com"), "SalesFlow_DW")
    with pytest.raises(ResetRefused):
        assert_reset_allowed(Settings(env="development"), "wrong")
    assert_reset_allowed(Settings(env="development", dw_db="SalesFlow_DW"), "SalesFlow_DW")
