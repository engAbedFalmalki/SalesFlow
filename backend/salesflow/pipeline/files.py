"""Ingest landing-zone files (catalog CSV snapshots, payment JSON batches).

Delivery rules:
* A file is identified by (name, SHA-256). Seeing the same pair again is not a
  new delivery and is ignored.
* Same bytes under a new name as an accepted file -> ``duplicate`` (skipped).
* Same name as an accepted file but different bytes -> ``conflict`` (held, not
  processed). Corrections must be delivered deliberately: as a new file name
  (payments) / a new catalog version, or under the name of a *rejected* file.
* Header / structure problems reject the whole file into quarantine.
"""

from __future__ import annotations

import csv
import io
import json
import re
from decimal import Decimal
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Connection

from ..db import get_engine
from .common import RunContext, canonical_json, sha256_bytes, sha256_text
from .raw import insert_raw, latest_checksums

PRODUCT_HEADER = ["product_sku", "product_name", "category", "list_price", "currency", "is_active", "updated_at"]
PRODUCT_FILE = re.compile(r"^products_v(\d{3,})\.csv$")
SOURCES = [
    ("product_catalog_csv", "products", "*.csv", "product"),
    ("payments_json", "payments", "*.json", "payment_txn"),
]
PREVIEW_CHARS = 4000


class FileRejected(Exception):
    def __init__(self, code: str, reason: str, reason_ar: str = ""):
        super().__init__(reason)
        self.code = code
        self.reason = reason
        self.reason_ar = reason_ar


def _sort_key(source: str, p: Path) -> tuple:
    if source == "product_catalog_csv":
        m = PRODUCT_FILE.match(p.name)
        return (int(m.group(1)) if m else 10**9, p.name)
    return (0, p.name)


def parse_products(name: str, data: bytes) -> tuple[int, list[tuple[str, dict]]]:
    m = PRODUCT_FILE.match(name)
    if not m:
        raise FileRejected("SCHEMA_INVALID_FILENAME",
                           f"Catalog files must be named products_vNNN.csv to carry a snapshot version; got '{name}'.",
                           f"يجب تسمية ملفات الكتالوج بصيغة products_vNNN.csv لتحمل رقم الإصدار، والاسم الحالي '{name}'.")
    try:
        content = data.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise FileRejected("SCHEMA_INVALID_ENCODING", f"File is not valid UTF-8: {e}",
                           f"ترميز الملف ليس UTF-8 صالحاً: {e}") from None
    reader = csv.reader(io.StringIO(content))
    header = next(reader, None)
    if header is None:
        raise FileRejected("SCHEMA_INVALID_HEADER", "File is empty: no header row.", "الملف فارغ ولا يحتوي صف العناوين.")
    header = [h.strip() for h in header]
    missing = [c for c in PRODUCT_HEADER if c not in header]
    unexpected = [c for c in header if c not in PRODUCT_HEADER]
    if missing or unexpected or len(set(header)) != len(header):
        parts, parts_ar = [], []
        if missing:
            parts.append(f"missing column(s): {', '.join(missing)}")
            parts_ar.append(f"أعمدة ناقصة: {', '.join(missing)}")
        if unexpected:
            parts.append(f"unexpected column(s): {', '.join(unexpected)}")
            parts_ar.append(f"أعمدة غير متوقعة: {', '.join(unexpected)}")
        if len(set(header)) != len(header):
            parts.append("duplicate column names")
            parts_ar.append("أسماء أعمدة مكررة")
        raise FileRejected("SCHEMA_INVALID_HEADER",
                           f"Catalog header does not match the contract ({'; '.join(parts)}). "
                           f"Expected: {', '.join(PRODUCT_HEADER)}.",
                           f"عناوين ملف الكتالوج لا تطابق العقد ({'، '.join(parts_ar)}). "
                           f"المتوقع: {', '.join(PRODUCT_HEADER)}.")
    records = []
    for i, row in enumerate(reader, start=1):
        if not any(cell.strip() for cell in row):
            continue
        if len(row) != len(header):
            rec = {"_raw_row": row, "_error": f"expected {len(header)} fields, found {len(row)}"}
        else:
            rec = dict(zip(header, row))
        sku = (rec.get("product_sku") or "").strip().upper()
        records.append((sku or f"{name}#{i}", rec))
    return int(m.group(1)), records


def parse_payments(name: str, data: bytes) -> list[tuple[str, object]]:
    try:
        doc = json.loads(data.decode("utf-8-sig"), parse_float=Decimal)
    except UnicodeDecodeError as e:
        raise FileRejected("SCHEMA_INVALID_ENCODING", f"File is not valid UTF-8: {e}",
                           f"ترميز الملف ليس UTF-8 صالحاً: {e}") from None
    except json.JSONDecodeError as e:
        raise FileRejected("SCHEMA_INVALID_JSON", f"File is not valid JSON ({e.msg} at line {e.lineno}, column {e.colno}).",
                           f"الملف ليس JSON صالحاً (خطأ في السطر {e.lineno}، العمود {e.colno}).") from None
    if not isinstance(doc, dict):
        raise FileRejected("SCHEMA_INVALID_STRUCTURE", "Top-level JSON value must be an object.",
                           "يجب أن تكون القيمة الرئيسية في ملف JSON كائناً.")
    if str(doc.get("schema_version")) != "1.0":
        raise FileRejected("SCHEMA_INVALID_STRUCTURE",
                           f"Unsupported schema_version {doc.get('schema_version')!r}; expected '1.0'.",
                           f"إصدار المخطط {doc.get('schema_version')!r} غير مدعوم، والمتوقع '1.0'.")
    txns = doc.get("transactions")
    if not isinstance(txns, list):
        found = type(txns).__name__ if txns is not None else 'nothing'
        raise FileRejected("SCHEMA_INVALID_STRUCTURE",
                           f"'transactions' must be an array; found {found}.",
                           f"يجب أن يكون الحقل 'transactions' مصفوفة، والموجود: {found}.")
    out = []
    for i, rec in enumerate(txns):
        tid = rec.get("transaction_id") if isinstance(rec, dict) else None
        out.append((tid.strip() if isinstance(tid, str) and tid.strip() else f"{name}#{i}", rec))
    return out


def _register(conn: Connection, ctx: RunContext, source: str, path: Path, chk: str, size: int, status: str,
              *, version: int | None = None, count: int | None = None, dup_of: int | None = None,
              message: str | None = None, message_ar: str | None = None) -> int:
    return conn.execute(text(
        """INSERT INTO meta.source_file (source_system, file_name, file_checksum, file_size_bytes, file_version,
                                         record_count, status, duplicate_of_file_id, first_seen_run_id, message, message_ar)
           OUTPUT inserted.file_id VALUES (:s, :n, :c, :sz, :v, :rc, :st, :d, :run, :m, :ma)"""),
        {"s": source, "n": path.name, "c": chk, "sz": size, "v": version, "rc": count, "st": status,
         "d": dup_of, "run": ctx.run_id, "m": message, "ma": message_ar}).scalar_one()


def run_ingest(ctx: RunContext) -> dict:
    eng = get_engine("dw", ctx.settings)
    summary = {"files_seen": 0, "new_deliveries": 0, "landed": 0, "duplicate": 0, "conflict": 0, "rejected": 0,
               "already_known": 0}
    for source, sub, pattern, entity in SOURCES:
        folder = ctx.settings.landing_dir / sub
        if not folder.exists():
            continue
        for path in sorted(folder.glob(pattern), key=lambda p: _sort_key(source, p)):
            if not path.is_file():
                continue
            summary["files_seen"] += 1
            data = path.read_bytes()
            chk = sha256_bytes(data)
            with eng.begin() as conn:
                known = conn.execute(text("SELECT file_id FROM meta.source_file WHERE file_name = :n AND file_checksum = :c"),
                                     {"n": path.name, "c": chk}).scalar()
                if known is not None:
                    summary["already_known"] += 1
                    continue
                summary["new_deliveries"] += 1
                same_bytes = conn.execute(text(
                    "SELECT file_id, file_name FROM meta.source_file WHERE file_checksum = :c AND status IN ('landed','processed')"),
                    {"c": chk}).first()
                if same_bytes is not None:
                    _register(conn, ctx, source, path, chk, len(data), "duplicate", dup_of=same_bytes[0],
                              message=f"Identical content to already accepted file '{same_bytes[1]}' "
                                      f"(file #{same_bytes[0]}). Skipped; no records re-read.",
                              message_ar=f"المحتوى مطابق للملف المقبول سابقاً '{same_bytes[1]}' (ملف رقم {same_bytes[0]}). "
                                         "تم تخطيه ولم تُقرأ سجلاته مرة أخرى.")
                    ctx.file_events.append({"file": path.name, "source": source, "status": "duplicate"})
                    summary["duplicate"] += 1
                    continue
                same_name = conn.execute(text(
                    "SELECT file_id FROM meta.source_file WHERE file_name = :n AND status IN ('landed','processed')"),
                    {"n": path.name}).scalar()
                if same_name is not None:
                    _register(conn, ctx, source, path, chk, len(data), "conflict", dup_of=same_name,
                              message="A different file with this name was already accepted. It was not processed. "
                                      "Deliver corrections as a new batch file name or catalog version.",
                              message_ar="سبق قبول ملف مختلف بنفس الاسم، لذلك لم يُعالج هذا الملف. "
                                         "أرسل التصحيحات باسم دفعة جديد أو بإصدار كتالوج جديد.")
                    ctx.file_events.append({"file": path.name, "source": source, "status": "conflict"})
                    summary["conflict"] += 1
                    continue
                try:
                    if source == "product_catalog_csv":
                        version, records = parse_products(path.name, data)
                    else:
                        version, records = None, parse_payments(path.name, data)
                except FileRejected as rej:
                    file_id = _register(conn, ctx, source, path, chk, len(data), "rejected", message=rej.reason,
                                        message_ar=rej.reason_ar or None)
                    preview = data[:PREVIEW_CHARS].decode("utf-8", errors="replace")
                    conn.execute(text(
                        """INSERT INTO qa.quarantine_record (entity, source_system, source_record_id, source_file_id,
                               source_file_name, run_id, error_code, error_reason, error_reason_ar, errors_json, payload,
                               payload_checksum, status, last_evaluated_run_id)
                           VALUES ('file', :s, :n, :f, :n, :run, :c, :r, :ra, :ej, :p, :chk, 'open', :run)"""),
                        {"s": source, "n": path.name, "f": file_id, "run": ctx.run_id, "c": rej.code, "r": rej.reason,
                         "ra": rej.reason_ar or None,
                         "ej": json.dumps([{"code": rej.code, "field": None, "message": rej.reason,
                                            "message_ar": rej.reason_ar}]),
                         "p": preview, "chk": chk})
                    ctx.file_events.append({"file": path.name, "source": source, "status": "rejected", "code": rej.code})
                    summary["rejected"] += 1
                    continue
                file_id = _register(conn, ctx, source, path, chk, len(data), "landed", version=version, count=len(records))
                st = ctx.stats[entity]
                st.rows_read += len(records)
                latest = latest_checksums(conn, entity)
                prepared = [(idx, rid, canonical_json(rec)) for idx, (rid, rec) in enumerate(records)]
                versions: dict[str, set[str]] = {}
                for _idx, rid, payload in prepared:
                    versions.setdefault(rid, set()).add(sha256_text(payload))
                seen: dict[str, set[str]] = {}
                new = []
                for idx, rid, payload in prepared:
                    pchk = sha256_text(payload)
                    if pchk in seen.get(rid, set()):
                        st.rows_duplicate += 1  # identical record repeated inside the file
                        continue
                    seen.setdefault(rid, set()).add(pchk)
                    # Conflicting in-file duplicates are all landed so staging can flag DUPLICATE_KEY.
                    if latest.get(rid) == pchk and len(versions[rid]) == 1:
                        st.rows_unchanged += 1
                        continue
                    new.append({"entity": entity, "source_system": source, "source_record_id": rid,
                                "source_updated_at_utc": None, "source_file_id": file_id,
                                "source_file_name": path.name, "file_checksum": chk, "record_index": idx,
                                "payload_json": payload, "payload_checksum": pchk})
                insert_raw(conn, ctx.run_id, new)
                st.rows_new_version += len(new)
                ctx.file_events.append({"file": path.name, "source": source, "status": "landed",
                                        "records": len(records), "new_versions": len(new)})
                summary["landed"] += 1
    return summary
