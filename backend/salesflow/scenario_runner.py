"""Apply a scenario and execute its manifest steps with the real pipeline."""

from __future__ import annotations

from sqlalchemy import text

from .config import Settings, get_settings
from .db import get_engine
from .generator.io import apply_scenario, load_manifest
from .pipeline.executor import default_worker_id, execute_run, run_now
from .pipeline.runs import claim, create_retry


def scenario_entry(scenario_id: str, settings: Settings | None = None) -> dict:
    m = load_manifest(settings)
    entry = next((s for s in m["scenarios"] if s["id"] == scenario_id), None)
    if entry is None:
        raise KeyError(scenario_id)
    return entry


def run_scenario(scenario_id: str, settings: Settings | None = None, requested_by: str = "scenario") -> list[dict]:
    """Apply source changes, then run each step synchronously. Returns step results."""
    settings = settings or get_settings()
    entry = scenario_entry(scenario_id, settings)
    apply_scenario(scenario_id, settings)
    results = []
    for step in entry["steps"]:
        if step["action"] == "run":
            run_id, status = run_now(step["mode"], requested_by, step.get("params") or {}, settings=settings)
        elif step["action"] == "retry_last_failed":
            with get_engine("dw", settings).connect() as conn:
                failed = conn.execute(text("SELECT MAX(run_id) FROM meta.pipeline_run WHERE status = 'failed'")).scalar()
            run_id = create_retry(failed, requested_by, settings)
            wid = default_worker_id(requested_by)
            claim(wid, run_id, settings)
            status = execute_run(run_id, wid, settings)
        else:
            raise ValueError(f"unknown step {step}")
        results.append({"step": step, "run_id": run_id, "status": status})
    return results
