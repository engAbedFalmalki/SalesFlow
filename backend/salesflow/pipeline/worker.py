"""Background worker: polls the durable run queue in SQL Server.

State lives only in the database, so a worker can be restarted at any time.
On each loop it first recovers runs whose worker died (stale heartbeat and no
pipeline lock held), then claims the oldest queued run.
"""

from __future__ import annotations

import logging
import threading

from ..config import Settings, get_settings
from .executor import default_worker_id, execute_run
from .runs import claim, recover_stale_runs

log = logging.getLogger(__name__)


def work_once(worker_id: str, settings: Settings) -> int | None:
    recover_stale_runs(settings)
    run_id = claim(worker_id, settings=settings)
    if run_id is not None:
        execute_run(run_id, worker_id, settings)
    return run_id


def run_worker(stop: threading.Event | None = None, settings: Settings | None = None, prefix: str = "worker") -> None:
    settings = settings or get_settings()
    stop = stop or threading.Event()
    worker_id = default_worker_id(prefix)
    log.info("worker started", extra={"worker": worker_id})
    while not stop.is_set():
        try:
            ran = work_once(worker_id, settings)
        except Exception:
            log.exception("worker loop error")
            ran = None
        if ran is None:
            stop.wait(settings.worker_poll_seconds)
    log.info("worker stopped", extra={"worker": worker_id})


def start_background_worker(settings: Settings | None = None) -> tuple[threading.Thread, threading.Event]:
    stop = threading.Event()
    t = threading.Thread(target=run_worker, kwargs={"stop": stop, "settings": settings, "prefix": "embedded"},
                         daemon=True, name="salesflow-worker")
    t.start()
    return t, stop
