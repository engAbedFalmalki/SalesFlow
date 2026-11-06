# Portfolio demo walkthrough (about 10 minutes)

Start from a clean baseline:

```powershell
cd backend
.\.venv\Scripts\python -m salesflow reset --confirm SalesFlow_DW
.\.venv\Scripts\python -m salesflow seed
.\.venv\Scripts\python -m salesflow run --mode initial
.\.venv\Scripts\python -m salesflow api          # terminal 1
cd ..\frontend; npm run dev                       # terminal 2
```

1. **Overview** (`/`). Point out the two KPI groups: *Sales, by order date* (solid green rule) and
   *Collections, by transaction date* (dashed rule). The same encoding is used for the chart lines. Switch
   the period to "Last 7 days" and the grain to "Week". Hover a KPI's info icon for its definition.
2. **Sales explorer** (`/sales`). Search order `1001`, open it: two lines (2 × 100, 1 × 300), payment
   500 on 1 Aug, refund 100 on 9 Aug, order value 500 vs net collections 400 shown separately.
   Filter by status "Cancelled", sort by ordered value, then **Export to Excel**: a formatted workbook
   with a totals row and an About sheet listing the filters.
3. **Pipeline runs** (`/pipeline`). Under "Simulate source changes", apply *New orders*, then press
   **Run incremental** and watch the stages complete from persisted state. Open the run: rows by entity,
   files seen, extraction window. Scenarios must be applied in the listed order.
4. Apply and run, one at a time: *Existing order updates* (statuses change, active ordered value drops),
   *Late-arriving payments* (October collections rise on the overview), *Duplicate file delivery*
   (open the run: 2 duplicates, 1 conflict, nothing loaded), *Catalog price update* (open order 1001
   again: unit price 100.00, "now 120.00").
5. Apply *Invalid product references* and *Invalid quantities, amounts and statuses* (run after each).
   **Data quality** (`/quality`): open records by reason; open `REFUND_EXCEEDS_COLLECTION` to see the
   reason and original payload; mark one record as ignored. Apply *Schema-invalid input* and run: three
   files rejected with explanations.
6. Apply *Corrected input and reprocessing*, run incremental, then on Data quality press **Reprocess**
   next to "Missing product ref". The six lines resolve and load. **Reconciliation**
   (`/reconciliation`): every difference is *explained* by quarantine; the orders-vs-collections panel is
   *informational* (unpaid cash-on-delivery orders, refunds).
7. **Failure and retry.** Apply *Pipeline interruption and retry*, press **Run with simulated failure**.
   The run fails at "Publish"; earlier stages are labelled as rolled back, the overview is unchanged,
   and the file stays "landed". Open the run and press **Retry this run**: 50 orders load exactly once.
8. **How it works** (`/architecture`). Diagram, sources, grains, metric definitions served by the API,
   dataset seed and scenario list, limitations.
9. **Test purchase.** On Sales explorer press **Add test purchase**, pick a customer and two products,
   choose *Failed, then paid*, and create it. The drawer shows the order written to the source DB, the
   payment file delivered, and the queued run; when it finishes, **Open the order** shows one failed and
   one successful payment, with collections equal to the order total.
10. **العربية.** Switch the language at the bottom of the sidebar: the whole app turns right-to-left
   Arabic, while numbers and dates keep Latin digits. Exports then use Arabic headers and labels.

Talking points: idempotency via checksums + state-based pending detection, atomic publish with
`READ_COMMITTED_SNAPSHOT`, watermark only on commit, durable queue with heartbeats, independent
oracles in tests.
