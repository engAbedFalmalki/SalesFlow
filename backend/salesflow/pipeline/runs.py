"""Durable run queue and run-state persistence (meta.pipeline_run*)."""

from __future__ import annotations

import json
import logging
import threading
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from ..config import Settings, get_settings
from ..db import get_engine
from .common import FAIL_POINTS, PipelineError

log = logging.getLogger(__name__)

STAGES: list[tuple[str, str]] = [
    ("extract_source_db", "Extract operational database"),
    ("ingest_files", "Ingest catalog and payment files"),
    ("stage_validate", "Stage, normalise and validate"),
    ("load_warehouse", "Load warehouse dimensions and facts"),
    ("quality_checks", "Warehouse quality gate"),
    ("reconcile", "Reconcile sources with warehouse"),
    ("publish", "Publish (commit) and advance watermarks"),
]
STAGE_SEQ = {name: i + 1 for i, (name, _) in enumerate(STAGES)}
MODES = ("initial", "incremental", "reprocess")
LOCK_RESOURCE = "salesflow_pipeline"


class ActiveRunExists(Exception):
    def __init__(self, run_id: int | None, status: str | None):
        super().__init__(f"Run {run_id} is already {status}")
        self.run_id = run_id
        self.status = status


def validate_request(mode: str, params: dict, settings: Settings) -> dict:
    if mode not in MODES:
        raise PipelineError("INVALID_REQUEST", f"Unknown mode {mode!r}")
    params = dict(params or {})
    allowed = {"fail_at", "scope", "error_codes", "entity", "source_run_id"}
    unknown = set(params) - allowed
    if unknown:
        raise PipelineError("INVALID_REQUEST", f"Unknown parameters: {sorted(unknown)}")
    if "fail_at" in params:
        if not settings.fault_injection_allowed:
            raise PipelineError("INVALID_REQUEST", "Fault injection is disabled outside development/test.")
        if params["fail_at"] not in FAIL_POINTS:
            raise PipelineError("INVALID_REQUEST", f"fail_at must be one of {FAIL_POINTS}")
    if mode == "reprocess":
        scope = params.get("scope")
        if scope not in ("quarantine", "run"):
            raise PipelineError("INVALID_REQUEST", "Reprocess requires scope 'quarantine' or 'run'.")
        if scope == "run" and not isinstance(params.get("source_run_id"), int):
            raise PipelineError("INVALID_REQUEST", "Reprocess scope 'run' requires an integer source_run_id.")
        if "error_codes" in params and not (isinstance(params["error_codes"], list)
                                            and all(isinstance(c, str) for c in params["error_codes"])):
            raise PipelineError("INVALID_REQUEST", "error_codes must be a list of strings.")
    elif {"scope", "error_codes", "entity", "source_run_id"} & set(params):
        raise PipelineError("INVALID_REQUEST", "Scope parameters are only valid for reprocess runs.")
    return params


def create_run(mode: str, requested_by: str, params: dict | None = None, retry_of: int | None = None,
               settings: Settings | None = None) -> int:
    """Insert a queued run. Raises ActiveRunExists if one is queued or running."""
    settings = settings or get_settings()
    params = validate_request(mode, params or {}, settings)
    eng = get_engine("dw", settings)
    try:
        with eng.begin() as conn:
            run_id = conn.execute(text(
                """INSERT INTO meta.pipeline_run (mode, status, requested_by, params_json, retry_of_run_id, active_slot)
                   OUTPUT inserted.run_id VALUES (:m, 'queued', :by, :p, :r, 1)"""),
                {"m": mode, "by": requested_by[:50], "p": json.dumps(params), "r": retry_of}).scalar_one()
            skip = {"extract_source_db", "ingest_files"} if mode == "reprocess" else set()
            for name, _label in STAGES:
                conn.execute(text(
                    "INSERT INTO meta.pipeline_run_stage (run_id, stage_seq, stage_name, status, message) "
                    "VALUES (:r, :s, :n, :st, :msg)"),
                    {"r": run_id, "s": STAGE_SEQ[name], "n": name, "st": "skipped" if name in skip else "pending",
                     "msg": "Not applicable to reprocess runs" if name in skip else None})
    except IntegrityError:
        with eng.connect() as conn:
            row = conn.execute(text(
                "SELECT TOP 1 run_id, status FROM meta.pipeline_run WHERE active_slot = 1")).first()
        raise ActiveRunExists(row[0] if row else None, row[1] if row else None) from None
    log.info("run queued", extra={"run_id": run_id, "mode": mode, "requested_by": requested_by})
    return run_id


def create_retry(run_id: int, requested_by: str, settings: Settings | None = None) -> int:
    settings = settings or get_settings()
    with get_engine("dw", settings).connect() as conn:
        row = conn.execute(text("SELECT mode, status, params_json FROM meta.pipeline_run WHERE run_id = :r"),
                           {"r": run_id}).first()
    if row is None:
        raise PipelineError("NOT_FOUND", f"Run {run_id} does not exist.")
    if row.status != "failed":
        raise PipelineError("INVALID_REQUEST", f"Only failed runs can be retried (run {run_id} is {row.status}).")
    params = json.loads(row.params_json or "{}")
    params.pop("fail_at", None)  # a retry never re-injects the original fault
    return create_run(row.mode, requested_by, params, retry_of=run_id, settings=settings)


def claim(worker_id: str, run_id: int | None = None, settings: Settings | None = None) -> int | None:
    """Atomically move a queued run to running. Returns the run id or None."""
    settings = settings or get_settings()
    with get_engine("dw", settings).begin() as conn:
        if run_id is None:
            sql = """WITH c AS (SELECT TOP (1) * FROM meta.pipeline_run WITH (UPDLOCK, READPAST, ROWLOCK)
                                WHERE status = 'queued' ORDER BY run_id)
                     UPDATE c SET status = 'running', worker_id = :w, started_at_utc = SYSUTCDATETIME(),
                                  heartbeat_at_utc = SYSUTCDATETIME()
                     OUTPUT inserted.run_id;"""
            return conn.execute(text(sql), {"w": worker_id}).scalar()
        return conn.execute(text(
            """UPDATE meta.pipeline_run SET status = 'running', worker_id = :w, started_at_utc = SYSUTCDATETIME(),
                      heartbeat_at_utc = SYSUTCDATETIME()
               OUTPUT inserted.run_id WHERE run_id = :r AND status = 'queued'"""),
            {"w": worker_id, "r": run_id}).scalar()


@contextmanager
def pipeline_lock(settings: Settings | None = None) -> Iterator[Connection | None]:
    """Session-scoped SQL Server application lock: one executing pipeline at a time.

    The lock lives on a dedicated connection; if the worker process dies, SQL
    Server ends the session and releases the lock automatically.
    """
    settings = settings or get_settings()
    conn = get_engine("dw", settings).connect().execution_options(isolation_level="AUTOCOMMIT")
    try:
        rc = conn.execute(text(
            "DECLARE @r INT; EXEC @r = sp_getapplock @Resource = :res, @LockMode = 'Exclusive', "
            "@LockOwner = 'Session', @LockTimeout = 0; SELECT @r"), {"res": LOCK_RESOURCE}).scalar()
        if rc is None or rc < 0:
            yield None
            return
        try:
            yield conn
        finally:
            conn.execute(text("EXEC sp_releaseapplock @Resource = :res, @LockOwner = 'Session'"), {"res": LOCK_RESOURCE})
    finally:
        conn.close()


class Tracker:
    """Writes stage progress on its own autocommit connection, so progress is
    visible while the publish transaction is still open."""

    def __init__(self, run_id: int, settings: Settings):
        self.run_id = run_id
        self._lock = threading.Lock()
        self._conn = get_engine("dw", settings).connect().execution_options(isolation_level="AUTOCOMMIT")

    def _exec(self, sql: str, **p) -> None:
        with self._lock:
            self._conn.execute(text(sql), {"run": self.run_id, **p})

    def heartbeat(self) -> None:
        self._exec("UPDATE meta.pipeline_run SET heartbeat_at_utc = SYSUTCDATETIME() WHERE run_id = :run AND status = 'running'")

    def start(self, stage: str) -> None:
        self._exec("UPDATE meta.pipeline_run_stage SET status = 'running', started_at_utc = SYSUTCDATETIME() "
                   "WHERE run_id = :run AND stage_seq = :s", s=STAGE_SEQ[stage])
        self._exec("UPDATE meta.pipeline_run SET current_stage = :n, heartbeat_at_utc = SYSUTCDATETIME() "
                   "WHERE run_id = :run", n=stage)

    def finish(self, stage: str, *, rows_read: int | None = None, rows_written: int | None = None,
               rows_rejected: int | None = None, message: str | None = None, details: dict | None = None) -> None:
        self._exec("""UPDATE meta.pipeline_run_stage SET status = 'succeeded', finished_at_utc = SYSUTCDATETIME(),
                          rows_read = :rr, rows_written = :rw, rows_rejected = :rj, message = :m, details_json = :d
                      WHERE run_id = :run AND stage_seq = :s""",
                   s=STAGE_SEQ[stage], rr=rows_read, rw=rows_written, rj=rows_rejected, m=message,
                   d=json.dumps(details, default=str) if details else None)

    def fail(self, stage: str | None, code: str, message: str) -> None:
        if stage:
            self._exec("""UPDATE meta.pipeline_run_stage SET status = 'failed', finished_at_utc = SYSUTCDATETIME(),
                              message = :m WHERE run_id = :run AND stage_seq = :s""", s=STAGE_SEQ[stage], m=message[:2000])
        self._exec("""UPDATE meta.pipeline_run_stage SET status = 'skipped', message = 'Not run because an earlier stage failed'
                      WHERE run_id = :run AND status IN ('pending', 'running')""")
        self._exec("""UPDATE meta.pipeline_run SET status = 'failed', active_slot = NULL, finished_at_utc = SYSUTCDATETIME(),
                          error_stage = :st, error_code = :c, error_message = :m
                      WHERE run_id = :run""", st=stage, c=code[:50], m=message[:4000])

    def mark_rolled_back(self, stages: list[str]) -> None:
        """Stages that completed inside the publish transaction were undone by its rollback."""
        for st in stages:
            self._exec("""UPDATE meta.pipeline_run_stage SET message = 'Completed, then rolled back: nothing from this stage was published'
                          WHERE run_id = :run AND stage_seq = :s AND status = 'succeeded'""", s=STAGE_SEQ[st])

    def close(self) -> None:
        self._conn.close()


def mark_failed(run_id: int, code: str, message: str, settings: Settings | None = None) -> None:
    t = Tracker(run_id, settings or get_settings())
    try:
        t.fail(None, code, message)
    finally:
        t.close()


def recover_stale_runs(settings: Settings | None = None) -> list[int]:
    """Fail 'running' runs whose worker stopped heart-beating and holds no lock."""
    settings = settings or get_settings()
    eng = get_engine("dw", settings)
    with eng.connect() as conn:
        stale = [r[0] for r in conn.execute(text(
            """SELECT run_id FROM meta.pipeline_run WHERE status = 'running'
               AND (heartbeat_at_utc IS NULL OR heartbeat_at_utc < DATEADD(SECOND, -:s, SYSUTCDATETIME()))"""),
            {"s": settings.stale_run_after_seconds})]
    recovered = []
    for run_id in stale:
        with pipeline_lock(settings) as lock:
            if lock is None:
                continue  # a live executor holds the lock; leave it alone
            with eng.connect() as conn:
                row = conn.execute(text("SELECT current_stage FROM meta.pipeline_run WHERE run_id = :r AND status = 'running'"),
                                   {"r": run_id}).first()
            if row is None:
                continue
            t = Tracker(run_id, settings)
            try:
                t.fail(row[0], "WORKER_INTERRUPTED",
                       "The worker stopped before finishing this run (no heartbeat). Uncommitted warehouse changes "
                       "were rolled back by the database; raw landings are kept. Retry the run.")
            finally:
                t.close()
            log.warning("recovered stale run", extra={"run_id": run_id})
            recovered.append(run_id)
    return recovered
