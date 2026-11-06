"""End-to-end check on the DEVELOPMENT databases at full size.

Resets this project's dev data, seeds the generated baseline, runs the initial
load, plays every scenario in manifest order with the real pipeline, then
compares warehouse totals (independent SQL) with the manifest expectations.

    python scripts/verify_full_dataset.py --confirm SalesFlow_DW
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

from helpers import expected_totals, open_quarantine_by_code, unresolved_reconciliation, warehouse_totals  # noqa: E402

from salesflow.config import get_settings  # noqa: E402
from salesflow.devtools import reset_project_data  # noqa: E402
from salesflow.generator.io import load_manifest, reset_landing, seed_ops  # noqa: E402
from salesflow.logging_setup import configure_logging  # noqa: E402
from salesflow.pipeline.executor import run_now  # noqa: E402
from salesflow.scenario_runner import run_scenario  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--confirm", required=True)
    args = ap.parse_args()
    configure_logging("WARNING", "text")
    s = get_settings()
    reset_project_data(args.confirm, s)
    seed_ops(s)
    reset_landing(s)
    m = load_manifest(s)
    t0 = time.monotonic()
    rid, status = run_now("initial", "verify", settings=s)
    print(f"s01_initial_load: run {rid} {status} ({time.monotonic() - t0:.1f}s)")
    ok = status == "succeeded" and warehouse_totals() == expected_totals(m)
    print(f"  baseline totals match manifest: {ok}")
    applied = []
    for sid in m["apply_order"][1:]:
        t0 = time.monotonic()
        results = run_scenario(sid, s, requested_by="verify")
        applied.append(sid)
        print(f"{sid}: " + ", ".join(f"run {r['run_id']} {r['status']}" for r in results) + f" ({time.monotonic() - t0:.1f}s)")
    exp = expected_totals(m, *applied)
    got = warehouse_totals()
    diffs = {k: (got[k], exp[k]) for k in exp if got[k] != exp[k]}
    print("final totals match manifest:", not diffs, diffs or "")
    print("open quarantine:", open_quarantine_by_code())
    unresolved = unresolved_reconciliation()
    print("unresolved reconciliation rows:", unresolved)
    return 0 if ok and not diffs and not unresolved else 1


if __name__ == "__main__":
    raise SystemExit(main())
