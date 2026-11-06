"""Extract changed rows from the operational SQL database into the raw layer.

Strategy (per table):
* Lower bound: last committed watermark minus an overlap window (initial runs
  read everything). The overlap re-reads rows whose transactions committed late
  with an older ``updated_at``; checksum deduplication makes re-reads harmless.
* Upper bound: ``MAX(updated_at)`` captured *before* reading, so rows modified
  during extraction are left for the next run instead of being half-read.
* Deterministic order: ``ORDER BY updated_at, <primary key>``. Rows sharing a
  timestamp are all inside or all outside the window, never split.
* The watermark is only advanced inside the publish transaction.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import text

from ..db import get_engine
from ..timeutil import iso_utc
from .common import RunContext, canonical_json, sha256_text
from .raw import insert_raw, latest_checksums

TABLES = [
    ("customer", "customers", "customer_id"),
    ("order", "orders", "order_id"),
    ("order_item", "order_items", "order_item_id"),
]


def watermark_name(table: str) -> str:
    return f"ops.{table}"


def _payload(row: dict) -> dict:
    out = {}
    for k, v in row.items():
        if isinstance(v, datetime):
            out[k] = iso_utc(v)
        elif isinstance(v, Decimal):
            out[k] = v
        else:
            out[k] = v
    return out


def run_extract(ctx: RunContext) -> dict:
    dw = get_engine("dw", ctx.settings)
    ops = get_engine("ops", ctx.settings)
    overlap = timedelta(seconds=ctx.settings.extract_overlap_seconds)
    details: dict[str, dict] = {}
    with dw.connect() as dconn:
        marks = {r[0]: r[1] for r in dconn.execute(text("SELECT source_name, watermark_utc FROM meta.watermark"))}
    for entity, table, key in TABLES:
        wm = None if ctx.mode == "initial" else marks.get(watermark_name(table))
        lower = (wm - overlap) if wm else None
        with ops.connect() as oconn:
            high = oconn.execute(text(f"SELECT MAX(updated_at) FROM dbo.{table}")).scalar()
            rows = []
            if high is not None:
                res = oconn.execute(text(
                    f"SELECT * FROM dbo.{table} WHERE (:lo IS NULL OR updated_at > :lo) AND updated_at <= :hi "
                    f"ORDER BY updated_at, {key}"), {"lo": lower, "hi": high})
                rows = [dict(r._mapping) for r in res]
        st = ctx.stats[entity]
        st.rows_read += len(rows)
        with dw.begin() as dconn:
            latest = latest_checksums(dconn, entity)
            new = []
            for r in rows:
                payload = canonical_json(_payload(r))
                chk = sha256_text(payload)
                rid = str(r[key])
                if latest.get(rid) == chk:
                    st.rows_unchanged += 1
                    continue
                latest[rid] = chk
                new.append({"entity": entity, "source_system": "ops_sql", "source_record_id": rid,
                            "source_updated_at_utc": r["updated_at"].isoformat(timespec="milliseconds"),
                            "source_file_id": None, "source_file_name": None, "file_checksum": None,
                            "record_index": None, "payload_json": payload, "payload_checksum": chk})
            insert_raw(dconn, ctx.run_id, new)
        st.rows_new_version += len(new)
        bounds = {"watermark_before": iso_utc(wm), "lower_bound_exclusive": iso_utc(lower),
                  "upper_bound_inclusive": iso_utc(high), "rows_read": len(rows), "new_versions": len(new),
                  "unchanged": len(rows) - len(new)}
        ctx.extract_bounds[table] = {"lower": lower, "upper": high}
        if high is not None and (wm is None or high > wm):
            ctx.new_watermarks[watermark_name(table)] = high
        details[table] = bounds
    return details
