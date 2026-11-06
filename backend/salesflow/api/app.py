"""SalesFlow HTTP API (FastAPI). OpenAPI docs at /docs.

Safety defaults: binds to 127.0.0.1, explicit CORS origins, parameterised SQL,
bounded pagination, structured errors. Mutating endpoints (run/retry/reprocess,
quarantine review, dev scenario apply) require mutations to be enabled and, when
SALESFLOW_ADMIN_TOKEN is set, a matching X-SalesFlow-Admin-Token header. In
production they are disabled unless a token is configured.
"""

from __future__ import annotations

import logging
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from .. import __version__
from ..analytics import definitions, queries
from ..config import Settings, get_settings
from ..logging_setup import configure_logging
from ..pipeline.common import PipelineError
from ..pipeline.runs import ActiveRunExists, create_retry, create_run

log = logging.getLogger("salesflow.api")
MAX_RANGE_DAYS = 731
Lang = Literal["en", "ar"]


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    configure_logging(s.log_level, s.log_format)
    stop = None
    if s.embedded_worker:
        from ..pipeline.worker import start_background_worker
        _t, stop = start_background_worker(s)
    yield
    if stop:
        stop.set()


app = FastAPI(title="SalesFlow API", version=__version__, lifespan=lifespan,
              description="Sales data warehouse & analytics over SYNTHETIC data. Money values are decimal strings in SAR; "
                          "timestamps are UTC; date parameters are Asia/Riyadh business dates.")
_settings = get_settings()


# --------------------------------------------------------------------------- errors & logging

def _err(status: int, code: str, message: str, details: object | None = None) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message, "details": details}})


@app.exception_handler(RequestValidationError)
async def _validation(_req: Request, exc: RequestValidationError):
    details = [{"loc": [str(x) for x in e.get("loc", [])], "msg": e.get("msg")} for e in exc.errors()]
    return _err(422, "VALIDATION_ERROR", "Request parameters are invalid.", details)


@app.exception_handler(HTTPException)
async def _http(_req: Request, exc: HTTPException):
    if isinstance(exc.detail, dict):
        return _err(exc.status_code, exc.detail.get("code", "ERROR"), exc.detail.get("message", ""), exc.detail.get("details"))
    return _err(exc.status_code, "ERROR", str(exc.detail))


@app.exception_handler(queries.NotFound)
async def _nf(_req: Request, exc: queries.NotFound):
    return _err(404, "NOT_FOUND", str(exc))


@app.exception_handler(PipelineError)
async def _pe(_req: Request, exc: PipelineError):
    return _err(404 if exc.code == "NOT_FOUND" else 400, exc.code, exc.message)


@app.exception_handler(Exception)
async def _unhandled(req: Request, exc: Exception):
    log.exception("unhandled error", extra={"path": req.url.path})
    return _err(500, "INTERNAL_ERROR", "Unexpected server error. See server logs for the request id.")


@app.middleware("http")
async def _access_log(request: Request, call_next):
    rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
    t0 = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:  # return a structured 500 that still passes through CORS
        log.exception("unhandled error", extra={"path": request.url.path, "request_id": rid})
        response = _err(500, "INTERNAL_ERROR", f"Unexpected server error (request id {rid}). See the API logs.")
    response.headers["x-request-id"] = rid
    log.info("request", extra={"request_id": rid, "method": request.method, "path": request.url.path,
                               "status": response.status_code, "ms": round((time.perf_counter() - t0) * 1000, 1)})
    return response


# --------------------------------------------------------------------------- dependencies

def require_mutations(x_salesflow_admin_token: Annotated[str | None, Header()] = None) -> Settings:
    s = get_settings()
    if not s.mutations_allowed:
        raise HTTPException(403, {"code": "READ_ONLY", "message": "This deployment is read-only; pipeline and review "
                                                                  "actions are disabled."})
    if s.admin_token is not None and not (
            x_salesflow_admin_token and secrets.compare_digest(x_salesflow_admin_token, s.admin_token.get_secret_value())):
        raise HTTPException(401, {"code": "UNAUTHORIZED", "message": "A valid X-SalesFlow-Admin-Token header is required."})
    return s


class DateRange(BaseModel):
    date_from: date
    date_to: date


def date_range(date_from: Annotated[date, Query(description="Asia/Riyadh business date, inclusive")],
               date_to: Annotated[date, Query(description="Asia/Riyadh business date, inclusive")]) -> DateRange:
    if date_to < date_from:
        raise HTTPException(422, {"code": "VALIDATION_ERROR", "message": "date_to must be on or after date_from."})
    if (date_to - date_from).days + 1 > MAX_RANGE_DAYS:
        raise HTTPException(422, {"code": "VALIDATION_ERROR", "message": f"Date ranges are limited to {MAX_RANGE_DAYS} days."})
    return DateRange(date_from=date_from, date_to=date_to)


def page_params(page: Annotated[int, Query(ge=1, le=100000)] = 1,
                page_size: Annotated[int, Query(ge=1, le=100)] = 25) -> tuple[int, int]:
    return page, min(page_size, get_settings().max_page_size)


Range = Annotated[DateRange, Depends(date_range)]
Paging = Annotated[tuple[int, int], Depends(page_params)]


# --------------------------------------------------------------------------- meta

@app.get("/", include_in_schema=False)
def root():
    """People who open the API's address in a browser land on the web app (API docs: /docs)."""
    return RedirectResponse(get_settings().web_url)


@app.get("/api/health", tags=["meta"])
def health():
    with queries.get_engine("dw").connect() as c:
        c.exec_driver_sql("SELECT 1")
    s = get_settings()
    return {"status": "ok", "version": __version__, "env": s.env, "read_only": not s.mutations_allowed,
            "requires_admin_token": s.admin_token is not None}


@app.get("/api/definitions", tags=["meta"])
def get_definitions(lang: Lang = "en"):
    from ..generator.io import load_manifest
    try:
        m = load_manifest()
        dataset = {"notice": definitions.NOTICE_AR if lang == "ar" else m["notice"], "generator": m["generator"],
                   "baseline": m["baseline"],
                   "scenarios": [{"id": s["id"], "title": s["title"], "description": s["description"]} for s in m["scenarios"]]}
    except FileNotFoundError:
        dataset = None
    return {**definitions.localized(lang), "dataset": dataset, "currency": "SAR", "business_timezone": "Asia/Riyadh"}


@app.get("/api/freshness", tags=["meta"])
def get_freshness():
    return queries.freshness()


# --------------------------------------------------------------------------- analytics

@app.get("/api/overview", tags=["analytics"])
def get_overview(r: Range):
    return queries.overview(r.date_from, r.date_to)


@app.get("/api/timeseries", tags=["analytics"])
def get_timeseries(r: Range, grain: Literal["day", "week", "month"] = "day"):
    return queries.timeseries(r.date_from, r.date_to, grain)


@app.get("/api/products/top", tags=["analytics"])
def get_top_products(r: Range, category: Annotated[str | None, Query(max_length=100)] = None,
                     limit: Annotated[int, Query(ge=1, le=50)] = 10):
    return queries.top_products(r.date_from, r.date_to, category, limit)


@app.get("/api/orders/status-distribution", tags=["analytics"])
def get_status_distribution(r: Range):
    return {"items": queries.status_distribution(r.date_from, r.date_to)}


@app.get("/api/filters", tags=["analytics"])
def get_filters():
    return queries.filter_options()


OrderStatus = Literal["pending", "processing", "shipped", "delivered", "cancelled"]


def order_filters(r: Range, status: Annotated[list[OrderStatus] | None, Query()] = None,
                  category: Annotated[str | None, Query(max_length=100)] = None,
                  product_sku: Annotated[str | None, Query(max_length=40)] = None,
                  region: Annotated[str | None, Query(max_length=60, description="Customer region (English name)")] = None,
                  city: Annotated[str | None, Query(max_length=100, description="Customer city (English name)")] = None,
                  order_id: Annotated[str | None, Query(max_length=12, pattern=r"^#?\d{1,10}$",
                                                        description="Order id or prefix, digits only")] = None) -> dict:
    return {"date_from": r.date_from, "date_to": r.date_to, "statuses": status, "category": category,
            "product_sku": product_sku, "region": region, "city": city,
            "order_id": int(order_id.lstrip("#")) if order_id else None}


@app.get("/api/orders", tags=["analytics"])
def get_orders(paging: Paging, f: Annotated[dict, Depends(order_filters)],
               sort: Literal["order_date", "order_id", "ordered_value", "net_collections", "status", "customer"] = "order_date",
               direction: Literal["asc", "desc"] = "desc"):
    page, size = paging
    return queries.list_orders(f, sort, direction, page, size)


@app.get("/api/orders/export.csv", tags=["analytics"])
def export_orders_csv(f: Annotated[dict, Depends(order_filters)], lang: Lang = "en"):
    """UTF-8 CSV with BOM; headers and labels in the requested language, Riyadh local times."""
    from ..analytics.export import iter_csv
    name = f"salesflow_orders_{f['date_from']}_{f['date_to']}.csv"
    return StreamingResponse(iter_csv(f, get_settings().max_export_rows, lang), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.get("/api/orders/export.xlsx", tags=["analytics"])
def export_orders_xlsx(f: Annotated[dict, Depends(order_filters)], lang: Lang = "en"):
    """Formatted Excel workbook: styled header, filters, number formats, totals and an About sheet."""
    from datetime import datetime, timezone
    from ..analytics.export import build_xlsx
    from ..timeutil import utc_to_riyadh
    data = build_xlsx(f, get_settings().max_export_rows, lang,
                      utc_to_riyadh(datetime.now(timezone.utc).replace(tzinfo=None)).replace(tzinfo=None))
    name = f"salesflow_orders_{f['date_from']}_{f['date_to']}.xlsx"
    return Response(content=data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.get("/api/customers", tags=["analytics"])
def get_customers(q: Annotated[str | None, Query(max_length=60)] = None,
                  limit: Annotated[int, Query(ge=1, le=50)] = 20):
    return {"items": queries.search_customers(q.strip() if q else None, limit)}


@app.get("/api/orders/{order_id}", tags=["analytics"])
def get_order(order_id: int):
    return queries.order_detail(order_id)


# --------------------------------------------------------------------------- pipeline

class RunRequest(BaseModel):
    mode: Literal["initial", "incremental"]
    # Development/test only (rejected otherwise): fail deliberately to demonstrate rollback and retry.
    fail_at: Literal["extract", "ingest", "staging", "load_warehouse", "before_commit"] | None = None


class ReprocessRequest(BaseModel):
    scope: Literal["quarantine", "run"]
    error_codes: list[Annotated[str, Field(max_length=50)]] | None = Field(default=None, max_length=30)
    entity: Literal["customer", "product", "order", "order_item", "payment_txn"] | None = None
    source_run_id: int | None = None


def _enqueue(fn):
    try:
        run_id = fn()
    except ActiveRunExists as e:
        raise HTTPException(409, {"code": "RUN_ACTIVE", "message": f"Run {e.run_id} is already {e.status}. "
                                  "Only one pipeline run can be queued or running at a time.",
                                  "details": {"run_id": e.run_id}}) from None
    return JSONResponse(status_code=202, content={"run_id": run_id, "status": "queued"})


@app.post("/api/pipeline/runs", tags=["pipeline"], status_code=202)
def post_run(body: RunRequest, _s: Annotated[Settings, Depends(require_mutations)]):
    return _enqueue(lambda: create_run(body.mode, "api", {"fail_at": body.fail_at} if body.fail_at else {}))


@app.post("/api/pipeline/runs/{run_id}/retry", tags=["pipeline"], status_code=202)
def post_retry(run_id: int, _s: Annotated[Settings, Depends(require_mutations)]):
    return _enqueue(lambda: create_retry(run_id, "api"))


@app.post("/api/pipeline/reprocess", tags=["pipeline"], status_code=202)
def post_reprocess(body: ReprocessRequest, _s: Annotated[Settings, Depends(require_mutations)]):
    params = body.model_dump(exclude_none=True)
    return _enqueue(lambda: create_run("reprocess", "api", params))


@app.get("/api/pipeline/runs", tags=["pipeline"])
def get_runs(paging: Paging, status: Literal["queued", "running", "succeeded", "failed"] | None = None):
    page, size = paging
    return queries.list_runs(page, size, status)


@app.get("/api/pipeline/runs/{run_id}", tags=["pipeline"])
def get_run(run_id: int):
    return queries.get_run(run_id)


@app.get("/api/pipeline/files", tags=["pipeline"])
def get_files(paging: Paging, status: Literal["landed", "processed", "duplicate", "rejected", "conflict"] | None = None):
    page, size = paging
    return queries.list_files(page, size, status)


# --------------------------------------------------------------------------- quality & reconciliation

@app.get("/api/quality", tags=["quality"])
def get_quality(run_id: int | None = None):
    return queries.quality_summary(run_id)


@app.get("/api/quality/quarantine", tags=["quality"])
def get_quarantine(paging: Paging,
                   entity: Literal["customer", "product", "order", "order_item", "payment_txn", "file"] | None = None,
                   error_code: Annotated[str | None, Query(max_length=50, pattern=r"^[A-Z_]+$")] = None,
                   status: Literal["open", "resolved", "superseded", "ignored"] | None = None,
                   run_id: int | None = None, q: Annotated[str | None, Query(max_length=100)] = None):
    page, size = paging
    return queries.list_quarantine({"entity": entity, "error_code": error_code, "status": status, "run_id": run_id,
                                    "q": q}, page, size)


@app.get("/api/quality/quarantine/{qid}", tags=["quality"])
def get_quarantine_record(qid: int):
    return queries.quarantine_detail(qid)


class QuarantineReview(BaseModel):
    status: Literal["ignored", "open"]
    note: Annotated[str | None, Field(max_length=500)] = None


@app.patch("/api/quality/quarantine/{qid}", tags=["quality"])
def patch_quarantine(qid: int, body: QuarantineReview, _s: Annotated[Settings, Depends(require_mutations)]):
    return queries.set_quarantine_status(qid, body.status, body.note)


@app.get("/api/reconciliation", tags=["reconciliation"])
def get_reconciliation(run_id: int | None = None):
    return queries.reconciliation(run_id)


# --------------------------------------------------------------------------- development scenarios

@app.get("/api/dev/scenarios", tags=["development"])
def get_scenarios():
    from ..generator.io import load_manifest
    try:
        m = load_manifest()
    except FileNotFoundError:
        return {"enabled": False, "items": []}
    s = get_settings()
    return {"enabled": s.env != "production" and s.mutations_allowed,
            "items": [{k: x[k] for k in ("id", "title", "description", "depends_on", "steps", "ops_changes", "files")}
                      for x in m["scenarios"]]}


@app.post("/api/dev/scenarios/{scenario_id}/apply", tags=["development"])
def post_apply_scenario(scenario_id: Annotated[str, Path(pattern=r"^s\d\d_[a-z0-9_]+$", max_length=60)],
                        _s: Annotated[Settings, Depends(require_mutations)]):
    """Apply a scenario's SOURCE changes only (ops rows + landing files). Run the pipeline afterwards."""
    if get_settings().env == "production":
        raise HTTPException(403, {"code": "FORBIDDEN", "message": "Scenarios are disabled in production."})
    from ..generator.io import apply_scenario
    try:
        return apply_scenario(scenario_id)
    except KeyError:
        raise HTTPException(404, {"code": "NOT_FOUND", "message": f"Unknown scenario {scenario_id}"}) from None
    except RuntimeError as e:
        raise HTTPException(409, {"code": "SCENARIO_ORDER", "message": str(e)}) from None





# --------------------------------------------------------------------------- development: manual purchases

class PurchaseLine(BaseModel):
    sku: Annotated[str, Field(min_length=1, max_length=40)]
    quantity: Annotated[int, Field(ge=1, le=99)]
    discount_amount: Annotated[Decimal, Field(ge=0, max_digits=10, decimal_places=2)] = Decimal("0.00")


class NewCustomer(BaseModel):
    first_name: Annotated[str, Field(min_length=1, max_length=100)]
    last_name: Annotated[str, Field(min_length=1, max_length=100)]
    email: Annotated[str, Field(min_length=3, max_length=200, pattern=r"^[^@\s]+@[^@\s]+$")]
    city: Annotated[str | None, Field(max_length=100)] = None


class PurchaseRequest(BaseModel):
    customer_id: int | None = None
    new_customer: NewCustomer | None = None
    lines: Annotated[list[PurchaseLine], Field(min_length=1, max_length=10)]
    channel: Literal["app", "web"] = "web"
    payment_method: Literal["mada", "credit_card", "apple_pay", "stc_pay", "cash_on_delivery"] = "mada"
    payment_outcome: Literal["paid", "failed_then_paid", "failed", "unpaid"] = "paid"
    run_pipeline: bool = True


@app.post("/api/dev/purchases", tags=["development"], status_code=201)
def post_purchase(body: PurchaseRequest, _s: Annotated[Settings, Depends(require_mutations)]):
    """Create a test purchase in the SOURCE systems (operational DB + payment file), then optionally queue a run."""
    from ..purchases import Line, PurchaseError, create_purchase
    if get_settings().env == "production":
        raise HTTPException(403, {"code": "FORBIDDEN", "message": "Test purchases are disabled in production."})
    if (body.customer_id is None) == (body.new_customer is None):
        raise HTTPException(422, {"code": "VALIDATION_ERROR", "message": "Choose an existing customer or enter a new one.",
                                  "details": {"message_ar": "اختر عميلاً موجوداً أو أدخل عميلاً جديداً."}})
    try:
        result = create_purchase(
            customer_id=body.customer_id,
            new_customer=body.new_customer.model_dump() if body.new_customer else None,
            lines=[Line(l.sku.strip().upper(), l.quantity, l.discount_amount.quantize(Decimal("0.01"))) for l in body.lines],
            channel=body.channel, method=body.payment_method, outcome=body.payment_outcome)
    except PurchaseError as e:
        raise HTTPException(404 if e.code == "NOT_FOUND" else 422,
                            {"code": e.code, "message": e.message, "details": {"message_ar": e.message_ar}}) from None
    result["run_id"] = None
    if body.run_pipeline:
        try:
            result["run_id"] = create_run("incremental", "api:purchase", {})
        except ActiveRunExists as e:
            result["run_note"] = {"code": "RUN_ACTIVE", "active_run_id": e.run_id}
    return JSONResponse(status_code=201, content=result)


# Registered last so it is the outermost middleware: every response, including
# structured 500s, carries the CORS headers the browser needs to read it.
app.add_middleware(CORSMiddleware, allow_origins=_settings.cors_origins, allow_credentials=False,
                   allow_methods=["GET", "POST", "PATCH"], allow_headers=["Content-Type", "X-SalesFlow-Admin-Token"],
                   expose_headers=["x-request-id", "content-disposition"])
