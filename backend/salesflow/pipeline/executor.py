"""Run execution. The CLI, the background worker and the tests all call
``execute_run``; there is exactly one implementation of the pipeline.

Transaction boundaries:
1. extract_source_db / ingest_files commit raw landings immediately (raw is an
   immutable, append-only record of what was received).
2. stage_validate .. publish run in ONE warehouse transaction. Staging,
   quarantine, dimensions, facts, quality results, reconciliation, watermarks,
   file statuses and the run's 'succeeded' status commit together, or not at
   all. Readers (READ_COMMITTED_SNAPSHOT) keep seeing the previous published
   state until the commit.
"""

from __future__ import annotations

import json
import logging
import socket
import threading
import time
import os

from sqlalchemy import text

from ..config import Settings, get_settings
from ..db import get_engine
from .common import PipelineError, RunContext
from .extract import run_extract
from .files import run_ingest
from .load import run_load
from .quality import run_quality
from .reconcile import run_reconcile
from .runs import STAGE_SEQ, Tracker, claim, create_run, pipeline_lock
from .staging import run_staging

log = logging.getLogger(__name__)


def default_worker_id(prefix: str = "worker") -> str:
    return f"{prefix}:{socket.gethostname()}:{os.getpid()}:{threading.get_ident()}"


class _Heartbeat(threading.Thread):
    def __init__(self, tracker: Tracker, interval: float):
        super().__init__(daemon=True)
        self.tracker, self.interval = tracker, interval
        self.stop_event = threading.Event()

    def run(self) -> None:
        while not self.stop_event.wait(self.interval):
            try:
                self.tracker.heartbeat()
            except Exception:  # pragma: no cover - heartbeat is best effort
                log.exception("heartbeat failed")

    def stop(self) -> None:
        self.stop_event.set()
        self.join(timeout=5)


def _warehouse_totals(conn) -> dict:
    r = conn.execute(text(
        """SELECT (SELECT COUNT(*) FROM dw.DimCustomer) customers, (SELECT COUNT(*) FROM dw.DimProduct) products,
                  (SELECT COUNT(*) FROM dw.DimOrder) orders, (SELECT COUNT(*) FROM dw.FactSalesLine) sales_lines,
                  (SELECT COUNT(*) FROM dw.FactPaymentTransaction) payment_transactions,
                  (SELECT ISNULL(SUM(NetAmount), 0) FROM dw.FactSalesLine) ordered_value,
                  (SELECT ISNULL(SUM(NetCollectionAmount), 0) FROM dw.FactPaymentTransaction) net_collections,
                  (SELECT COUNT(*) FROM qa.quarantine_record WHERE status = 'open') open_quarantine""")).first()
    return {k: (str(v) if not isinstance(v, int) else v) for k, v in r._mapping.items()}


def execute_run(run_id: int, worker_id: str, settings: Settings | None = None) -> str:
    """Execute a claimed (status='running') run to completion. Returns final status."""
    settings = settings or get_settings()
    eng = get_engine("dw", settings)
    with eng.connect() as conn:
        row = conn.execute(text("SELECT mode, status, params_json FROM meta.pipeline_run WHERE run_id = :r"),
                           {"r": run_id}).first()
    if row is None or row.status != "running":
        raise PipelineError("INVALID_STATE", f"Run {run_id} is not claimed for execution.")
    tracker = Tracker(run_id, settings)
    with pipeline_lock(settings) as lock:
        if lock is None:
            tracker.fail(None, "CONCURRENT_RUN", "Another pipeline execution holds the pipeline lock.")
            tracker.close()
            return "failed"
        ctx = RunContext(run_id=run_id, mode=row.mode, params=json.loads(row.params_json or "{}"), settings=settings)
        hb = _Heartbeat(tracker, settings.heartbeat_seconds)
        hb.start()
        stage: str | None = None
        t0 = time.monotonic()
        log.info("run started", extra={"run_id": run_id, "mode": ctx.mode, "worker": worker_id})
        try:
            if ctx.mode in ("initial", "incremental"):
                stage = "extract_source_db"
                tracker.start(stage)
                d = run_extract(ctx)
                ctx.check_fail_point("extract")
                tracker.finish(stage, rows_read=sum(v["rows_read"] for v in d.values()),
                               rows_written=sum(v["new_versions"] for v in d.values()),
                               message="New or changed rows landed in raw.source_record", details=d)
                stage = "ingest_files"
                tracker.start(stage)
                d = run_ingest(ctx)
                ctx.check_fail_point("ingest")
                tracker.finish(stage, rows_read=d["files_seen"], rows_written=d["landed"],
                               rows_rejected=d["rejected"] + d["conflict"],
                               message=f"{d['new_deliveries']} new deliveries: {d['landed']} landed, "
                                       f"{d['duplicate']} duplicate, {d['conflict']} conflict, {d['rejected']} rejected",
                               details={**d, "files": ctx.file_events})
            with eng.begin() as tx:
                stage = "stage_validate"
                tracker.start(stage)
                d = run_staging(tx, ctx)
                ctx.check_fail_point("staging")
                tracker.finish(stage, rows_read=sum(v["evaluated"] for v in d.values()),
                               rows_written=sum(v["accepted"] for v in d.values()),
                               rows_rejected=sum(v["rejected"] for v in d.values()), details=d)
                stage = "load_warehouse"
                tracker.start(stage)
                d = run_load(tx, ctx)
                ctx.check_fail_point("load_warehouse")
                tracker.finish(stage, rows_read=sum(v["candidates"] for v in d.values()),
                               rows_written=sum(v["inserted"] + v["updated"] for v in d.values()), details=d)
                stage = "quality_checks"
                tracker.start(stage)
                d = run_quality(tx, ctx)
                tracker.finish(stage, rows_rejected=d["record_failures"], message="Warehouse quality gate passed",
                               details=d)
                stage = "reconcile"
                tracker.start(stage)
                d = run_reconcile(tx, ctx)
                tracker.finish(stage, rows_read=d["results"], details=d)
                stage = "publish"
                tracker.start(stage)
                _publish(tx, ctx)
                ctx.check_fail_point("before_commit")
                hb.stop()
                duration = round(time.monotonic() - t0, 3)
                ctx.summary = {"duration_seconds": duration, "entity_stats": ctx.stats_dict(),
                               "warehouse_totals": _warehouse_totals(tx),
                               "watermarks": {k: v.isoformat() for k, v in ctx.new_watermarks.items()}}
                tx.execute(text(
                    """UPDATE meta.pipeline_run_stage SET status = 'succeeded', finished_at_utc = SYSUTCDATETIME(),
                              message = 'Committed atomically with all warehouse changes'
                       WHERE run_id = :r AND stage_seq = :s"""), {"r": run_id, "s": STAGE_SEQ["publish"]})
                tx.execute(text(
                    """UPDATE meta.pipeline_run SET status = 'succeeded', active_slot = NULL, current_stage = NULL,
                              finished_at_utc = SYSUTCDATETIME(), heartbeat_at_utc = SYSUTCDATETIME(),
                              summary_json = :s WHERE run_id = :r"""),
                    {"r": run_id, "s": json.dumps(ctx.summary, default=str)})
            log.info("run succeeded", extra={"run_id": run_id, "duration_s": ctx.summary["duration_seconds"]})
            return "succeeded"
        except PipelineError as e:
            hb.stop()
            log.warning("run failed", extra={"run_id": run_id, "stage": stage, "code": e.code, "error": e.message})
            tracker.fail(stage, e.code, e.message)
            _note_rollback(tracker, stage)
            return "failed"
        except Exception as e:  # unexpected: keep a readable message, log the traceback
            hb.stop()
            log.exception("run crashed", extra={"run_id": run_id, "stage": stage})
            tracker.fail(stage, "UNEXPECTED_ERROR", f"{type(e).__name__}: {str(e)[:3500]}")
            _note_rollback(tracker, stage)
            return "failed"
        finally:
            tracker.close()


TX_STAGES = ["stage_validate", "load_warehouse", "quality_checks", "reconcile", "publish"]


def _note_rollback(tracker: Tracker, failed_stage: str | None) -> None:
    if failed_stage in TX_STAGES:
        tracker.mark_rolled_back(TX_STAGES[:TX_STAGES.index(failed_stage)])


def _publish(tx, ctx: RunContext) -> None:
    for entity, s in ctx.stats.items():
        tx.execute(text(
            """INSERT INTO meta.run_entity_stats (run_id, entity, rows_read, rows_new_version, rows_unchanged,
                   rows_duplicate, rows_evaluated, rows_accepted, rows_rejected, rows_inserted, rows_updated,
                   rows_dw_unchanged)
               VALUES (:run, :e, :a, :b, :c, :d, :f, :g, :h, :i, :j, :k)"""),
            {"run": ctx.run_id, "e": entity, "a": s.rows_read, "b": s.rows_new_version, "c": s.rows_unchanged,
             "d": s.rows_duplicate, "f": s.rows_evaluated, "g": s.rows_accepted, "h": s.rows_rejected,
             "i": s.rows_inserted, "j": s.rows_updated, "k": s.rows_dw_unchanged})
    for name, value in ctx.new_watermarks.items():
        tx.execute(text(
            """MERGE meta.watermark AS t USING (SELECT :n AS source_name, :v AS watermark_utc) AS s
               ON t.source_name = s.source_name
               WHEN MATCHED AND (t.watermark_utc IS NULL OR s.watermark_utc > t.watermark_utc) THEN
                   UPDATE SET watermark_utc = s.watermark_utc, last_run_id = :run, updated_at_utc = SYSUTCDATETIME()
               WHEN NOT MATCHED THEN INSERT (source_name, watermark_utc, last_run_id) VALUES (s.source_name, s.watermark_utc, :run);"""),
            {"n": name, "v": value, "run": ctx.run_id})
    if ctx.mode in ("initial", "incremental"):
        tx.execute(text("UPDATE meta.source_file SET status = 'processed', processed_run_id = :run WHERE status = 'landed'"),
                   {"run": ctx.run_id})


def run_now(mode: str, requested_by: str = "cli", params: dict | None = None, retry_of: int | None = None,
            settings: Settings | None = None) -> tuple[int, str]:
    """Create a run and execute it synchronously in this process (CLI / tests)."""
    settings = settings or get_settings()
    run_id = create_run(mode, requested_by, params, retry_of=retry_of, settings=settings)
    wid = default_worker_id(requested_by)
    if claim(wid, run_id, settings) is None:
        raise PipelineError("INVALID_STATE", f"Run {run_id} was claimed by another worker.")
    return run_id, execute_run(run_id, wid, settings)
