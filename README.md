# SalesFlow: Sales Data Warehouse & Analytics

A portfolio project showing practical data engineering and analytics: three synthetic source systems,
a raw → staging → warehouse pipeline in SQL Server with quarantine, quality gates and reconciliation,
a FastAPI read/ops API, and a Next.js analytics app that reads only from the real warehouse.

> **Synthetic data only.** Every customer, order, product and payment is produced by a deterministic
> generator (seed `20261101`). Names are fictional, emails use `example.com`, and the currency is SAR.

| | |
|---|---|
| Sources | Operational SQL database (customers, orders, order lines), versioned catalog CSV files, payment/refund JSON files |
| Pipeline | Python 3.12, SQLAlchemy 2.1 + pyodbc, one executor shared by CLI and API, durable SQL Server run queue, background worker |
| Warehouse | SQL Server: `raw`, `stg`, `dw` (DimDate, DimCustomer, DimProduct, DimOrder, FactSalesLine, FactPaymentTransaction), `qa`, `meta` |
| API | FastAPI 0.156 (OpenAPI at `/docs`) |
| Web | Next.js 16, React 19, TypeScript, Tailwind CSS 4, Recharts 3, TanStack Query, Radix UI |
| Motion | GSAP 3.15 + ScrollTrigger, Lenis 1.3 smooth scroll, React Bits components (AnimatedContent, BlurText, CountUp, LightRays, SplitText, SpotlightCard, StarBorder) |
| Tests | pytest (61 tests, real SQL Server), Playwright (16 browser tests against the running stack) |

Docs: [architecture](docs/architecture.md) · [data dictionary](docs/data-dictionary.md) ·
[metric definitions](docs/metrics.md) · [demo walkthrough](docs/demo-walkthrough.md)

---

## 1. Prerequisites (verified setup)

Verified on Windows 10 with:

* SQL Server 2025 Express (`localhost\SQLEXPRESS`), Windows authentication, ODBC Driver 18 for SQL Server
* Python 3.12.10
* Node.js 24 / npm 11
* Microsoft Edge (used by Playwright, so no browser download is needed)

PostgreSQL is **not** supported; SQL Server ran in the build environment so no fallback was needed.
`docker-compose.yml` (SQL Server 2022 + API + worker + web) is included but **was not run**: Docker was
not available on the build machine.

## 2. Setup

All commands are PowerShell from the repository root unless noted.

```powershell
# configuration (defaults target localhost\SQLEXPRESS with Windows auth)
Copy-Item .env.example .env

# backend
cd backend
py -3.12 -m venv .venv            # or: python -m venv .venv
.\.venv\Scripts\pip install -r requirements.lock
cd ..

# frontend
cd frontend
npm ci
cd ..
```

## 3. Create databases and apply migrations

```powershell
cd backend
.\.venv\Scripts\python -m salesflow migrate
```

Creates `SalesFlow_Ops` (source) and `SalesFlow_DW` (warehouse, with `READ_COMMITTED_SNAPSHOT` on) and
applies the versioned SQL files in `backend/salesflow/migrations/`. Re-running is a no-op.

## 4. Generate data and run the initial load

```powershell
.\.venv\Scripts\python -m salesflow generate          # data/baseline + data/scenarios (deterministic)
.\.venv\Scripts\python -m salesflow seed              # load the source DB, deliver baseline files to data/landing
.\.venv\Scripts\python -m salesflow run --mode initial
```

Expected: about 18 s; 2,000 customers, 500 products, 10,000 orders, 19,889 order lines and
10,811 payment transactions loaded, 0 quarantined, every reconciliation row `matched`.
`--seed`, `--orders`, `--customers` and `--products` change the generated volume.

## 5. Run an incremental scenario

```powershell
.\.venv\Scripts\python -m salesflow scenario list
.\.venv\Scripts\python -m salesflow scenario apply s02_new_orders
.\.venv\Scripts\python -m salesflow run --mode incremental
```

Scenarios must be applied in order (their source timestamps move forward). Some need special steps:

```powershell
.\.venv\Scripts\python -m salesflow reprocess --quarantine --error-code MISSING_PRODUCT_REF   # after s10
.\.venv\Scripts\python -m salesflow run --mode incremental --fail-at before_commit           # s11 failure
.\.venv\Scripts\python -m salesflow retry <failed_run_id>
```

Each scenario's expected outcome is in `data/scenarios/manifest.json`.

## 6. Start the application

Two terminals:

```powershell
cd backend; .\.venv\Scripts\python -m salesflow api      # http://127.0.0.1:8000 (docs at /docs)
```

```powershell
cd frontend; npm run dev                                 # http://localhost:3000
```

With `SALESFLOW_EMBEDDED_WORKER=true` (the `.env.example` default) the API process also runs the
background worker. To run the worker separately, set it to `false` and start
`python -m salesflow worker`. Runs started from the web app are queued in SQL Server and picked up
within ~2 s.

## 7. Run the tests

```powershell
cd backend
.\.venv\Scripts\python -m pytest            # ~3 min; uses SalesFlow_Ops_Test / SalesFlow_DW_Test
```

```powershell
cd frontend
npm run typecheck
npx playwright test                         # needs the API and web app running with data loaded
```

Backend tests create and reset their own `*_Test` databases and a temporary data folder; they never
touch the development databases. The browser tests read the development warehouse through the real API.

## 8. Reset this project's development data

```powershell
cd backend
.\.venv\Scripts\python -m salesflow reset --confirm SalesFlow_DW
```

Deletes rows from SalesFlow tables only (schemas, migrations and `DimDate` stay) and empties
`data/landing`. It refuses to run unless `SALESFLOW_ENV` is `development`/`test`, both databases are named
`SalesFlow_*`, the SQL Server is local (or listed in `SALESFLOW_RESET_ALLOWED_SERVERS`), no run is in
progress, and `--confirm` equals the warehouse name. There is no reset API endpoint.

---

## Hosted demo on Netlify (no server)

Live at **https://salesflow-sa.netlify.app**. Netlify runs only the Next.js site, so this build has no API and
no database: `backend/scripts/export_static.py` exports the warehouse to `frontend/public/static-data/`
(exact read-only API responses for runs, quality, reconciliation and definitions, plus compact order, line and
payment arrays), and `frontend/src/lib/static-api.ts` computes the overview, trend, top products, order list,
order details and the Excel workbook in the browser with the same rules and rounding as the API. It is a
read-only snapshot: pipeline, reprocess, review and test-purchase actions are disabled, and the data changes
only when you export and deploy again. A Node check compared it with the live API on 187 requests (all
equal).

To update it after the warehouse changes (Netlify CLI, signed in with `netlify login`):

```powershell
.\backend\.venv\Scripts\python backend\scripts\export_static.py
cd frontend
npx netlify-cli deploy --prod --site salesflow-sa
```

`frontend/netlify.toml` sets the static mode (`NEXT_PUBLIC_STATIC_DATA=1`) and builds into `.next-netlify`,
so a running local dev server doesn't block the build.

## Share a public link (from this PC)

```powershell
powershell -ExecutionPolicy Bypass -File scripts\public-link.ps1           # add -Rebuild after changing the site
```

The script builds a separate copy of the site (`frontend/.next-public`) that calls `/api` on its own
address, starts a **read-only** API on port 8001 (`SALESFLOW_ENV=production`, `READ_ONLY=true`, so every
write endpoint answers 403) and the site on port 3001, then opens a Cloudflare quick tunnel and prints a
`https://….trycloudflare.com` link. Anyone with the link can browse the dashboard and download Excel
exports; nobody can run the pipeline, reprocess or add purchases. The local development servers on
8000/3000 are not affected. The link works only while the PC and the script are running, changes on
every start, and quick tunnels have no uptime guarantee. Ctrl+C stops sharing. Needs `cloudflared`
(`winget install --id Cloudflare.cloudflared`).

## Motion and smooth scrolling

* **Lenis** (`frontend/src/components/motion/smooth-scroll.tsx`) smooths page scrolling and is driven by the
  GSAP ticker so ScrollTrigger stays in sync. Nested scroll areas (tables, the quarantine reasons list) keep
  native scrolling (`allowNestedScroll`), drawers and tooltips are excluded, and Lenis pauses while a
  drawer locks the page.
* **GSAP ScrollTrigger**: sections rise in once when scrolled into view; product and status bars grow to
  their own values.
* **React Bits** (copied into `frontend/src/components/reactbits/` with their MIT + Commons Clause notice):
  `SplitText` page titles, `CountUp` KPI figures, `AnimatedContent` section reveals, `SpotlightCard` on
  the summary panels (one local change: no forced radius or padding). On the Overview (home) page the title
  comes into focus with `BlurText` (local change: a `tag` prop so it stays an `<h1>`), and faint `LightRays`
  in the accent colour sit behind the header (WebGL2, no pointer tracking, fading out before the content).
  Each page's main action (Run incremental, Reprocess open records, Export to Excel) has a slow `StarBorder`
  light; buttons inside dialogs and drawers stay plain. Colours come from the CSS tokens in `globals.css`.
* **Reduced motion**: with `prefers-reduced-motion: reduce`, smoothing is off (`data-smooth-scroll="off"`
  on `<html>`) and content appears without animation: the title shows at once, the light rays and the
  button light hold still.
* **Exact numbers**: counters are display-only and land on the same text as the static formatter; the
  exact decimal stays in each figure's `title` and in the API.

## Language, exports and test purchases

* **English / العربية.** The switch at the bottom of the sidebar (and the mobile menu) changes the whole UI to
  Arabic with a right-to-left layout. The choice is stored in the `sf_lang` cookie and read on the server, so
  pages render in the chosen language on first load. Texts live in `frontend/src/lib/dict.ts`; the API sends
  Arabic variants for its own texts (`message_ar`, `error_reason_ar`, `/api/definitions?lang=ar`).
* **Latin digits everywhere.** Numbers, dates and amounts always use 0-9, also in Arabic (`ar-u-nu-latn`
  locale; amounts as `1,234.50 ر.س`). Date fields are text fields in `YYYY-MM-DD` with a calendar
  popover instead of the browser's date input, whose digits follow the OS locale. Digits typed on an
  Arabic keyboard (٠-٩) are converted to 0-9.
* **Exports** (Sales explorer): **Export to Excel** builds a formatted `.xlsx` (header row, frozen
  header, filters, number and date formats, a totals row, and an About sheet listing the filters,
  export time and notes). It uses the active filters and the UI language for headers and labels. Times
  are Asia/Riyadh local, and amounts are numbers. For other tools, the same data is available as CSV
  from the API only (`GET /api/orders/export.csv`, UTF-8 with BOM); the page has no CSV button.
* **Add test purchase** (Sales explorer, development only). It creates a real order in the operational
  database and writes a payment file to `data/landing/payments/` (`payments_manual_<order>.json`). By
  default it also queues an incremental run, so you can watch the purchase go through extraction,
  validation and loading. You can pick an existing or new customer, up to 10 products with quantities and
  discounts, the channel, the payment method, and an outcome (paid, failed then paid, failed only,
  unpaid; cash on delivery starts unpaid). The endpoint is `POST /api/dev/purchases`. It is guarded like
  the other mutations and refused when `SALESFLOW_ENV=production`.
* **Region and city.** Customers live in cities across all 13 Saudi regions. The reference list
  (English and Arabic names, region, generator weight) is `backend/salesflow/geo.py`. The source system
  only stores the city; the load step derives `dw.DimCustomer.Region` from it on every run, which also fills
  rows loaded before the column existed. A city that is not in the list keeps an empty region. The Sales
  explorer filters by **Region** and **City** (the city list follows the chosen region), each order shows its
  customer's city, and the Excel export has Region and City columns (in Arabic when the UI is Arabic).
  Test purchases pick the new customer's city from the same list.
* **Reprocessing feedback.** After **Reprocess** on the Data quality page, the page follows the run and,
  when it ends, says how many records passed (loaded into the warehouse and closed) and how many still
  fail; then it refreshes every view. Reprocessing only re-checks records against the current accepted
  data: a record whose own content is invalid (wrong currency, negative amount, …) fails again until a
  corrected version arrives from the source, or until it is marked as ignored.
* **Migrations** `V003` (Arabic message columns), `V004` (a covering index for "latest raw version"
  lookups) and `V005` (`DimCustomer.Region`) apply with `python -m salesflow migrate`.

### SQL Server Express on a machine short of memory

When Windows reports low memory, SQL Server shrinks itself; on the build machine it fell to about 300 MB,
with a query-memory pool of about 10 MB. Queries whose memory request doesn't fit then wait up to about
5 minutes before running anyway. The pipeline's heavier statements carry
`OPTION (MAX_GRANT_PERCENT = 10)` (`pipeline/common.py`), and V004 lets the staging lookup stream from an
index, so runs no longer stall. Dashboard queries can still be slow in that state. To keep a working
minimum, set `min server memory` on the instance (for example 1024 MB, via `sp_configure`) or free up RAM.
The build machine's `SQLEXPRESS` instance now uses `min server memory = 1024`, which raised the
query-memory pool from about 10 MB to about 500 MB. To undo it:

```sql
EXEC sp_configure 'show advanced options', 1; RECONFIGURE;
EXEC sp_configure 'min server memory (MB)', 0; RECONFIGURE;
EXEC sp_configure 'show advanced options', 0; RECONFIGURE;
```

## Required test coverage

| # | Requirement | Test |
|---|---|---|
| 1 | Initial load counts and totals | `test_pipeline_scenarios::test_initial_load_expected_counts_and_totals` |
| 2 | Same batch twice, no duplicates | `test_repeating_the_same_batch_creates_no_duplicates` |
| 3 | Order update reaches warehouse | `test_existing_order_update_reaches_warehouse` |
| 4 | Late payment included | `test_late_arriving_payment_is_included_on_its_transaction_date` |
| 5 | Duplicate file delivery | `test_duplicate_file_delivery_is_detected_and_skipped` |
| 6 | Invalid records quarantined with reasons | `test_invalid_records_are_quarantined_with_reasons`, `test_invalid_product_references_are_quarantined`, `test_schema_invalid_files_are_rejected_with_explanations` |
| 7 | Failed run keeps watermark | `test_failure_before_commit_does_not_advance_watermark` (4 failure points) |
| 8 | Retry after failure | `test_failed_run_keeps_watermark_and_warehouse_then_retry_succeeds`, `test_interrupted_worker_is_recovered_and_retry_succeeds` |
| 9 | Lines × payments not multiplied | `test_multiple_lines_and_payments_do_not_multiply_amounts` |
| 10 | Historical prices unchanged | `test_historical_sales_prices_unchanged_after_catalog_price_update` |
| 11 | Cancelled orders | `test_cancelled_orders_follow_metric_definitions`, `test_cancelled_order_in_ordered_value_but_not_active` |
| 12 | Partial refunds / Order 1001 | `test_order_1001_lifetime_values`, `test_payment_and_refund_on_different_dates` |
| 13 | Timezone and date-field rules | `test_date_filters_use_riyadh_business_dates`, `test_previous_period_comparison_is_equal_length_and_explicit` |
| 14 | API totals vs independent SQL | `test_api::test_api_totals_match_independent_sql`, `test_order_listing_filters_sorting_pagination_and_export` |
| 15 | UI pages and filters on real backend | `frontend/e2e/app.spec.ts` (16 tests, incl. smooth scroll, reduced motion, Arabic/RTL, exports and a test purchase) |

Also covered: corrected input + reprocessing, one-active-run enforcement, pipeline lock, API → worker
execution, read-only and token protection, input validation, generator determinism, reset guards.

## Key decisions and assumptions

* **Modular monolith.** One Python package (`salesflow`) holds generator, pipeline, analytics and API.
  CLI, background worker and tests all call `pipeline.executor.execute_run`.
* **Durable queue in SQL Server.** `meta.pipeline_run` is the source of truth. A filtered unique
  index allows one queued/running run; `sp_getapplock` prevents two executors; heartbeats let a worker
  fail runs whose process died (`WORKER_INTERRUPTED`), which can then be retried.
* **Atomic publish.** Raw landing commits first (immutable, append-only). Staging, quarantine,
  dimensions, facts, quality results, reconciliation, watermarks, file statuses and the run's
  `succeeded` status commit in one transaction. With `READ_COMMITTED_SNAPSHOT`, dashboards keep showing
  the previous state until the commit.
* **State-based pending detection.** A raw version is processed when it differs from the accepted
  staging row and is not already quarantined. Retries therefore pick up everything a failed run
  landed, and nothing is processed twice.
* **Extraction window.** `(watermark − 5 min overlap, MAX(updated_at) captured before reading]`,
  ordered by `(updated_at, primary key)`, deduplicated by payload checksum.
* **Source DB has no constraints**, simulating a legacy app, so invalid quantities, prices and
  references reach the pipeline and are quarantined there.
* **Type 1 dimensions** (customer, product, order header). Historical category reporting uses the
  latest category; Type 2 is future work. Sales amounts always use the unit price stored on the line.
* **Money** is `DECIMAL` in SQL and `Decimal` in Python, and travels as strings in JSON. The UI groups
  digits on the string and converts to numbers only to draw charts.
* **Out of scope:** tax, shipping, multi-currency, payment-to-product allocation, hard deletes.

## Verification status

Verified on the build machine:

* Migrations, generator, initial load (18 s for 10k orders), all 11 scenarios via CLI, API and UI.
* `pytest`: 61 passed. Playwright: 16 passed (run alone; running it at the same time as `pytest` on one
  machine overloads SQL Server and the dev server and causes timeouts).
* Exports opened as files: Excel workbook (English and Arabic, right-to-left sheet) and the API CSV.
* Test purchases through the UI and the API, each loaded by an incremental run and checked in the order
  drawer (order 11451: 5,067.90 SAR ordered and collected).
* The UI was exercised in a browser at desktop (1440 px) and mobile (375 px) widths, including
  scenario application, run progress, failure and retry, quarantine review and reprocessing.

Not verified:

* `docker-compose.yml` and the Dockerfiles (Docker not installed).
* SQL authentication mode (only Windows authentication was exercised).
* Production deployment and the production build served behind a reverse proxy. `next build`
  succeeds, but the e2e tests ran against `next dev`.

## Known limitations and future improvements

See also the "Known limitations" section in the app's How it works page.

* Type 2 history for customers, products and order status.
* Hard-delete detection (CDC or soft-delete flags).
* Change detection and reconciliation load per-entity key sets into Python memory. That suits this
  scale (tens of thousands of rows); larger volumes should push the diff into SQL or use CDC/change tracking.
* A newer source version that fails validation leaves the previous accepted version in the warehouse;
  the UI surfaces the open quarantine record, but there is no "stale" flag on warehouse rows yet.
* Authentication is a single shared admin token for mutations; a real deployment needs user identity
  and audit trails.
* Scheduling (cron or event triggers), alerting on failed runs, and data-retention policies for raw.
