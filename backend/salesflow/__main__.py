"""SalesFlow command line. Run ``python -m salesflow --help``."""

from __future__ import annotations

import argparse
import json
import sys

from .config import get_settings
from .logging_setup import configure_logging


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, default=str))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="salesflow", description="SalesFlow data warehouse CLI (synthetic data)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("migrate", help="Create the databases if needed and apply SQL migrations")

    g = sub.add_parser("generate", help="Generate the deterministic baseline and scenarios into data/")
    g.add_argument("--seed", type=int, default=20261101)
    g.add_argument("--orders", type=int, default=10000)
    g.add_argument("--customers", type=int, default=2000)
    g.add_argument("--products", type=int, default=500)

    sub.add_parser("seed", help="Load the baseline into the operational DB and deliver baseline files to landing/")
    sc = sub.add_parser("scenario", help="List or apply source-change scenarios")
    sc_sub = sc.add_subparsers(dest="scmd", required=True)
    sc_sub.add_parser("list")
    sa = sc_sub.add_parser("apply")
    sa.add_argument("scenario_id")
    sa.add_argument("--force", action="store_true", help="Apply even if timestamps are out of order")

    r = sub.add_parser("run", help="Run the pipeline synchronously in this process")
    r.add_argument("--mode", choices=["initial", "incremental"], default="incremental")
    r.add_argument("--fail-at", choices=["extract", "ingest", "staging", "load_warehouse", "before_commit"],
                   help="Development fault injection")
    e = sub.add_parser("enqueue", help="Queue a run for the background worker")
    e.add_argument("--mode", choices=["initial", "incremental"], default="incremental")
    rt = sub.add_parser("retry", help="Retry a failed run (synchronously)")
    rt.add_argument("run_id", type=int)
    rp = sub.add_parser("reprocess", help="Re-evaluate quarantined records or one run's raw records")
    grp = rp.add_mutually_exclusive_group(required=True)
    grp.add_argument("--quarantine", action="store_true", help="Open quarantine records")
    grp.add_argument("--source-run", type=int, help="Raw records landed by this run")
    rp.add_argument("--error-code", action="append", dest="error_codes")
    rp.add_argument("--entity")
    sub.add_parser("worker", help="Run the background worker (polls the durable queue)")
    sub.add_parser("status", help="Show recent runs")
    api = sub.add_parser("api", help="Start the API server (uvicorn)")
    api.add_argument("--reload", action="store_true")
    rs = sub.add_parser("reset", help="DEV ONLY: delete this project's data (keeps schemas)")
    rs.add_argument("--confirm", help="Must equal the warehouse database name")
    rs.add_argument("--keep-ops", action="store_true")

    args = ap.parse_args(argv)
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format if args.cmd in ("api", "worker") else "text")

    if args.cmd == "migrate":
        from .migrate import migrate
        _print({"applied": migrate(settings)})
    elif args.cmd == "generate":
        from .generator.baseline import GenConfig
        from .generator.io import write_dataset
        m = write_dataset(GenConfig(seed=args.seed, orders=args.orders, customers=args.customers,
                                    products=args.products), settings)
        _print({"baseline": m["baseline"], "scenarios": m["apply_order"]})
    elif args.cmd == "seed":
        from .generator.io import reset_landing, seed_ops
        _print({"ops_rows": seed_ops(settings), "landing_files": reset_landing(settings)})
    elif args.cmd == "scenario":
        from .generator.io import apply_scenario, load_manifest
        if args.scmd == "list":
            for s in load_manifest(settings)["scenarios"]:
                print(f"{s['id']:<28} {s['title']}")
        else:
            _print(apply_scenario(args.scenario_id, settings, force=args.force))
    elif args.cmd in ("run", "retry", "reprocess"):
        from .pipeline.executor import run_now
        from .pipeline.runs import create_retry, claim
        from .pipeline.executor import execute_run, default_worker_id
        if args.cmd == "run":
            params = {"fail_at": args.fail_at} if args.fail_at else {}
            run_id, status = run_now(args.mode, "cli", params, settings=settings)
        elif args.cmd == "retry":
            run_id = create_retry(args.run_id, "cli", settings)
            wid = default_worker_id("cli")
            claim(wid, run_id, settings)
            status = execute_run(run_id, wid, settings)
        else:
            params = ({"scope": "quarantine"} if args.quarantine else {"scope": "run", "source_run_id": args.source_run})
            if args.error_codes:
                params["error_codes"] = args.error_codes
            if args.entity:
                params["entity"] = args.entity
            run_id, status = run_now("reprocess", "cli", params, settings=settings)
        _print(_run_report(run_id))
        return 0 if status == "succeeded" else 1
    elif args.cmd == "enqueue":
        from .pipeline.runs import create_run
        _print({"run_id": create_run(args.mode, "cli", {}, settings=settings), "status": "queued"})
    elif args.cmd == "worker":
        from .pipeline.worker import run_worker
        try:
            run_worker(settings=settings)
        except KeyboardInterrupt:
            pass
    elif args.cmd == "status":
        from .analytics import queries
        _print(queries.list_runs(page=1, page_size=10)["items"])
    elif args.cmd == "api":
        import uvicorn
        uvicorn.run("salesflow.api.app:app", host=settings.api_host, port=settings.api_port, reload=args.reload,
                    log_config=None)
    elif args.cmd == "reset":
        from .devtools import ResetRefused, reset_project_data
        try:
            _print(reset_project_data(args.confirm, settings, include_ops=not args.keep_ops))
        except ResetRefused as e:
            print(f"Refused: {e}", file=sys.stderr)
            return 2
    return 0


def _run_report(run_id: int) -> dict:
    from .analytics import queries
    d = queries.get_run(run_id)
    return {
        "run_id": d["run_id"], "mode": d["mode"], "status": d["status"],
        "duration_seconds": d["duration_seconds"], "error": d["error"],
        "stages": [{"stage": s["stage_name"], "status": s["status"], "read": s["rows_read"],
                    "written": s["rows_written"], "rejected": s["rows_rejected"]} for s in d["stages"]],
        "entity_stats": d["entity_stats"],
    }


if __name__ == "__main__":
    sys.exit(main())
