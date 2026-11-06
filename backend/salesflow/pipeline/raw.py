"""Raw-layer helpers: latest-version lookup and append-only inserts."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Connection

from ..db import bulk_json_param, chunked

_INSERT = """
INSERT INTO raw.source_record (entity, source_system, source_record_id, source_updated_at_utc, source_file_id,
                               source_file_name, file_checksum, record_index, payload_json, payload_checksum, run_id)
SELECT entity, source_system, source_record_id, source_updated_at_utc, source_file_id, source_file_name,
       file_checksum, record_index, payload_json, payload_checksum, :run
FROM OPENJSON(:j) WITH (
    entity NVARCHAR(30), source_system NVARCHAR(30), source_record_id NVARCHAR(100),
    source_updated_at_utc DATETIME2(3), source_file_id INT, source_file_name NVARCHAR(260),
    file_checksum CHAR(64), record_index INT, payload_json NVARCHAR(MAX), payload_checksum CHAR(64))
"""


def latest_checksums(conn: Connection, entity: str) -> dict[str, str]:
    """Latest raw payload checksum per source record id for one entity."""
    rows = conn.execute(text(
        """SELECT source_record_id, payload_checksum FROM (
               SELECT source_record_id, payload_checksum,
                      ROW_NUMBER() OVER (PARTITION BY source_record_id ORDER BY raw_id DESC) AS rn
               FROM raw.source_record WHERE entity = :e) x WHERE rn = 1"""), {"e": entity})
    return {r[0]: r[1] for r in rows}


def insert_raw(conn: Connection, run_id: int, records: list[dict]) -> int:
    for chunk in chunked(records, 4000):
        conn.execute(text(_INSERT), {"run": run_id, "j": bulk_json_param(chunk)})
    return len(records)
