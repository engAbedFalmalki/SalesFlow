"""Shared pipeline types: run context, errors, canonical payloads, counters."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from ..config import Settings

ENTITIES = ("customer", "product", "order", "order_item", "payment_txn")
FAIL_POINTS = ("extract", "ingest", "staging", "load_warehouse", "before_commit")


# Query hint for the pipeline's heavier statements. On SQL Server Express under
# memory pressure the query-memory pool can shrink to a few MB; an uncapped grant
# request then queues for up to ~5 minutes before running anyway. Capped, the
# statement starts at once and any overflow spills to tempdb, which is cheap at
# these data sizes.
GRANT_CAP = "OPTION (MAX_GRANT_PERCENT = 10)"


class PipelineError(Exception):
    """An expected, explainable pipeline failure with a stable error code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class InjectedFailure(PipelineError):
    def __init__(self, point: str):
        super().__init__("INJECTED_FAILURE", f"Deliberate failure injected at '{point}' (development fault injection).")


@dataclass
class EntityStats:
    rows_read: int = 0
    rows_new_version: int = 0
    rows_unchanged: int = 0
    rows_duplicate: int = 0
    rows_evaluated: int = 0
    rows_accepted: int = 0
    rows_rejected: int = 0
    rows_inserted: int = 0
    rows_updated: int = 0
    rows_dw_unchanged: int = 0


@dataclass
class RunContext:
    run_id: int
    mode: str
    params: dict
    settings: Settings
    stats: dict[str, EntityStats] = field(default_factory=lambda: defaultdict(EntityStats))
    new_watermarks: dict[str, datetime] = field(default_factory=dict)
    extract_bounds: dict[str, dict] = field(default_factory=dict)
    file_events: list[dict] = field(default_factory=list)       # registry outcomes this run
    record_failures: dict[tuple[str, str], int] = field(default_factory=lambda: defaultdict(int))
    evaluated: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    warehouse_checks: list[dict] = field(default_factory=list)
    summary: dict = field(default_factory=dict)

    def check_fail_point(self, point: str) -> None:
        if self.params.get("fail_at") == point:
            raise InjectedFailure(point)

    def stats_dict(self) -> dict[str, dict]:
        return {k: asdict(v) for k, v in self.stats.items()}


def _canon(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, Decimal):
        return format(v, "f")  # exact decimal literal, never binary float
    if isinstance(v, (int, float)):
        return json.dumps(v)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, dict):
        return "{" + ",".join(f"{json.dumps(str(k), ensure_ascii=False)}:{_canon(v[k])}" for k in sorted(v)) + "}"
    if isinstance(v, (list, tuple)):
        return "[" + ",".join(_canon(x) for x in v) + "]"
    return json.dumps(str(v), ensure_ascii=False)


def canonical_json(payload: Any) -> str:
    """Deterministic JSON (sorted keys, exact decimals) used for raw payloads."""
    return _canon(payload)


def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def load_payload(s: str) -> Any:
    """Parse a raw payload, keeping numbers exact (Decimal for non-integers)."""
    return json.loads(s, parse_float=Decimal)
