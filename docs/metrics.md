# Metric definitions

Single source in code: `backend/salesflow/analytics/definitions.py` (served by `GET /api/definitions`).
Independent reference implementation: `backend/salesflow/generator/reference.py`.

| Metric | Definition | Date basis | SQL |
|---|---|---|---|
| Gross merchandise value | Σ quantity × unit price recorded on the order line, before discounts; includes cancelled orders | order date | `SUM(FactSalesLine.GrossAmount)` |
| Ordered value | GMV − line discounts; includes cancelled orders | order date | `SUM(FactSalesLine.NetAmount)` |
| Active ordered value | Ordered value of orders whose current status is not `cancelled` | order date | `SUM(NetAmount) WHERE IsCancelled = 0` |
| Orders (non-cancelled) | Distinct non-cancelled orders with ≥ 1 accepted line | order date | `COUNT(DISTINCT OrderKey)` |
| Average order value | Active ordered value ÷ non-cancelled orders; `n/a` when 0 orders | order date | |
| Successful collections | Σ amount of `payment` transactions with status `succeeded` | transaction date | |
| Successful refunds | Σ amount of `refund` transactions with status `succeeded` | transaction date | |
| Net collections | Successful collections − successful refunds | transaction date | `SUM(NetCollectionAmount)` |

## Rules

* **Not revenue.** Collections are cash movements, not recognised accounting revenue or profit.
* **Excluded:** tax, shipping, multi-currency. All amounts are SAR, `DECIMAL(18,2)`.
* **Amount convention:** every transaction amount is positive; `TransactionType` gives the direction.
  `NetCollectionAmount` is a persisted column: `+Amount` (successful payment), `−Amount` (successful
  refund), `0` (failed or pending).
* **Dates:** stored UTC; business dates are Asia/Riyadh (UTC+3, no DST). Sales filter by `OrderDateKey`,
  collections by `TransactionDateKey`.
* **Status:** metrics use the order's *current* status (Type 1). An order cancelled in November is
  excluded from active ordered value on its original August order date.
* **No fan-out:** sales-line and payment facts are never joined row-to-row. The order listing aggregates
  each to order grain in separate CTEs; the time series aggregates each to date-bucket grain.
* **No product allocation** of payments or refunds. Product analytics use sales lines only.
* **Comparison periods** use the immediately preceding window of equal length. When that window starts
  before the first available data, comparison is reported as unavailable with a reason. A previous value
  of 0 yields an absolute change but no percentage.

## Worked example (Order 1001, part of the generated baseline)

| Line | Qty | Unit price | Discount |
|---|---|---|---|
| SKU-00001 | 2 | 100.00 | 0 |
| SKU-00002 | 1 | 300.00 | 0 |

Payment `PAY-…` succeeded 500.00 on 1 Aug 2026; refund `RFD-…` succeeded 100.00 on 9 Aug 2026.

| Lifetime | Value |
|---|---|
| Gross merchandise value | 500.00 |
| Ordered value | 500.00 |
| Successful collections | 500.00 |
| Successful refunds | 100.00 |
| Net collections | 400.00 |

By period: 1 Aug shows collections 500 / refunds 0; 9 Aug shows collections 0 / refunds 100 /
net −100 with no sales; August as a whole nets 400. After the catalog update (s06) SKU-00001's list
price becomes 120.00 while the line keeps 100.00.
