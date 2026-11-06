"""Write generated datasets to disk, seed the operational DB, apply scenarios."""

from __future__ import annotations

import csv
import json
import logging
import shutil
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import text

from ..config import Settings, get_settings
from ..db import chunked, get_engine
from .baseline import Baseline, GenConfig, generate_baseline
from .scenarios import baseline_payment_files, build_scenarios, products_csv

log = logging.getLogger(__name__)

OPS_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "customers": [("customer_id", "INT"), ("customer_code", "NVARCHAR(20)"), ("first_name", "NVARCHAR(100)"),
                  ("last_name", "NVARCHAR(100)"), ("email", "NVARCHAR(200)"), ("city", "NVARCHAR(100)"),
                  ("created_at", "DATETIME2(3)"), ("updated_at", "DATETIME2(3)")],
    "orders": [("order_id", "INT"), ("customer_id", "INT"), ("order_placed_at", "DATETIME2(3)"),
               ("status", "NVARCHAR(30)"), ("currency", "NVARCHAR(10)"), ("channel", "NVARCHAR(20)"),
               ("created_at", "DATETIME2(3)"), ("updated_at", "DATETIME2(3)")],
    "order_items": [("order_item_id", "INT"), ("order_id", "INT"), ("line_number", "INT"),
                    ("product_sku", "NVARCHAR(40)"), ("quantity", "INT"), ("unit_price", "DECIMAL(12,2)"),
                    ("discount_amount", "DECIMAL(12,2)"), ("created_at", "DATETIME2(3)"), ("updated_at", "DATETIME2(3)")],
}
OPS_KEYS = {"customers": "customer_id", "orders": "order_id", "order_items": "order_item_id"}
SYNTHETIC_NOTICE = ("SYNTHETIC DATA. Every customer, order, product and payment in this dataset was generated "
                    "by salesflow.generator from a fixed random seed. No real people or transactions.")


def _ser(v: object) -> object:
    if isinstance(v, datetime):
        return v.isoformat(timespec="milliseconds")
    if isinstance(v, Decimal):
        return f"{v:.2f}"
    return v


def _ser_row(table: str, row: dict) -> dict:
    return {c: _ser(row.get(c)) for c, _ in OPS_COLUMNS[table]}


def _clean_dir(path: Path, root: Path) -> None:
    path = path.resolve()
    if root.resolve() not in path.parents:
        raise RuntimeError(f"refusing to clean {path}: outside {root}")
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)


def write_dataset(cfg: GenConfig, settings: Settings | None = None) -> dict:
    """Generate the baseline + scenarios and write them under ``data_dir``."""
    settings = settings or get_settings()
    b: Baseline = generate_baseline(cfg)
    scenarios = build_scenarios(b)
    data = settings.data_dir
    data.mkdir(parents=True, exist_ok=True)
    base_dir, scen_dir = settings.baseline_dir, settings.scenarios_dir
    _clean_dir(base_dir, data)
    _clean_dir(scen_dir, data)

    (base_dir / "ops").mkdir()
    for table, rows in (("customers", b.customers), ("orders", b.orders), ("order_items", b.items)):
        with open(base_dir / "ops" / f"{table}.csv", "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh, lineterminator="\n")
            cols = [c for c, _ in OPS_COLUMNS[table]]
            w.writerow(cols)
            for r in rows:
                sr = _ser_row(table, r)
                w.writerow(["" if sr[c] is None else sr[c] for c in cols])
    (base_dir / "products").mkdir()
    (base_dir / "products" / "products_v001.csv").write_text(products_csv(b.products), encoding="utf-8", newline="")
    (base_dir / "payments").mkdir()
    for name, content in baseline_payment_files(b).items():
        (base_dir / "payments" / name).write_text(content, encoding="utf-8", newline="")

    for s in scenarios:
        d = scen_dir / s.id
        d.mkdir()
        changes = {t: [_ser_row(t, r) for r in rows] for t, rows in s.ops_upserts.items()}
        (d / "ops_changes.json").write_text(json.dumps(changes, indent=1), encoding="utf-8")
        for rel, content in s.files.items():
            p = d / "files" / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8", newline="")

    manifest = {
        "notice": SYNTHETIC_NOTICE,
        "generator": cfg.to_dict(),
        "currency": "SAR",
        "business_timezone": "Asia/Riyadh",
        "baseline": {
            "ops_tables": {"customers": len(b.customers), "orders": len(b.orders), "order_items": len(b.items)},
            "product_files": ["products_v001.csv"],
            "payment_files": len(baseline_payment_files(b)),
        },
        "apply_order": [s.id for s in scenarios],
        "scenarios": [s.manifest_entry() for s in scenarios],
    }
    (scen_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    log.info("dataset written", extra={"data_dir": str(data), "orders": len(b.orders), "items": len(b.items)})
    return manifest


def load_manifest(settings: Settings | None = None) -> dict:
    settings = settings or get_settings()
    path = settings.scenarios_dir / "manifest.json"
    if not path.exists():
        raise FileNotFoundError("No dataset generated yet. Run: python -m salesflow data generate")
    return json.loads(path.read_text(encoding="utf-8"))


def _upsert_ops(conn, table: str, rows: list[dict]) -> None:
    cols = OPS_COLUMNS[table]
    key = OPS_KEYS[table]
    with_clause = ", ".join(f"{c} {t}" for c, t in cols)
    names = ", ".join(c for c, _ in cols)
    sets = ", ".join(f"t.{c} = s.{c}" for c, _ in cols if c != key)
    sql = (f"MERGE dbo.{table} AS t USING (SELECT * FROM OPENJSON(:j) WITH ({with_clause})) AS s "
           f"ON t.{key} = s.{key} WHEN MATCHED THEN UPDATE SET {sets} "
           f"WHEN NOT MATCHED THEN INSERT ({names}) VALUES ({', '.join('s.' + c for c, _ in cols)});")
    for chunk in chunked(rows, 5000):
        conn.execute(text(sql), {"j": json.dumps(chunk)})


def seed_ops(settings: Settings | None = None) -> dict:
    """Replace the operational DB contents with the generated baseline."""
    settings = settings or get_settings()
    base = settings.baseline_dir / "ops"
    counts = {}
    with get_engine("ops", settings).begin() as conn:
        for table in ("order_items", "orders", "customers"):
            conn.execute(text(f"DELETE FROM dbo.{table}"))
        for table in ("customers", "orders", "order_items"):
            with open(base / f"{table}.csv", newline="", encoding="utf-8") as fh:
                rows = [{k: (v if v != "" else None) for k, v in r.items()} for r in csv.DictReader(fh)]
            _upsert_ops(conn, table, rows)
            counts[table] = len(rows)
    return counts


def reset_landing(settings: Settings | None = None) -> int:
    """Empty the landing zone and deliver the baseline files into it."""
    settings = settings or get_settings()
    _clean_dir(settings.landing_dir, settings.data_dir)
    n = 0
    for sub in ("products", "payments"):
        (settings.landing_dir / sub).mkdir()
        for f in sorted((settings.baseline_dir / sub).glob("*")):
            shutil.copy2(f, settings.landing_dir / sub / f.name)
            n += 1
    return n


def apply_scenario(scenario_id: str, settings: Settings | None = None, force: bool = False) -> dict:
    """Apply a scenario's source changes: ops upserts + landing-zone deliveries."""
    settings = settings or get_settings()
    manifest = load_manifest(settings)
    entry = next((s for s in manifest["scenarios"] if s["id"] == scenario_id), None)
    if entry is None:
        raise KeyError(f"Unknown scenario {scenario_id!r}")
    if scenario_id == "s01_initial_load":
        return {"ops": seed_ops(settings), "files": reset_landing(settings)}
    d = settings.scenarios_dir / scenario_id
    changes = json.loads((d / "ops_changes.json").read_text(encoding="utf-8"))
    eng = get_engine("ops", settings)
    with eng.begin() as conn:
        min_upd = min((r["updated_at"] for rows in changes.values() for r in rows), default=None)
        if min_upd and not force:
            current_max = conn.execute(text(
                "SELECT MAX(m) FROM (SELECT MAX(updated_at) m FROM dbo.customers UNION ALL "
                "SELECT MAX(updated_at) FROM dbo.orders UNION ALL SELECT MAX(updated_at) FROM dbo.order_items) x"
            )).scalar()
            # Re-applying the same scenario is harmless (identical upserts). Applying an
            # *older* scenario after a newer one would move the source clock backwards and
            # its rows would fall behind the extraction watermark.
            if current_max and datetime.fromisoformat(min_upd) <= current_max and not _already_applied(conn, changes):
                raise RuntimeError(
                    f"Scenario {scenario_id} has modification timestamps older than data already in the "
                    "source. Apply scenarios in manifest order (or reset), or pass force=True.")
        for table in ("customers", "orders", "order_items"):
            if changes.get(table):
                _upsert_ops(conn, table, changes[table])
    copied = []
    files_dir = d / "files"
    if files_dir.exists():
        for f in sorted(files_dir.rglob("*")):
            if f.is_file():
                dest = settings.landing_dir / f.relative_to(files_dir)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, dest)
                copied.append(str(f.relative_to(files_dir)).replace("\\", "/"))
    return {"ops": {t: len(r) for t, r in changes.items()}, "files": copied}


def _already_applied(conn, changes: dict) -> bool:
    """True when every scenario row already exists with identical modification time."""
    for table, rows in changes.items():
        for r in rows[:50]:
            got = conn.execute(text(f"SELECT updated_at FROM dbo.{table} WHERE {OPS_KEYS[table]} = :k"),
                               {"k": r[OPS_KEYS[table]]}).scalar()
            if got is None or got != datetime.fromisoformat(r["updated_at"]):
                return False
    return True
