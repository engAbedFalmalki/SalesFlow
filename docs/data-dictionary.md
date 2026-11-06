# Data dictionary

All timestamps ending in `_utc` / `Utc` are UTC. Money is `DECIMAL(18,2)` SAR.

## Source: `SalesFlow_Ops` (operational, no business constraints by design)

| Table | Columns |
|---|---|
| `dbo.customers` | customer_id (PK), customer_code, first_name, last_name, email, city, created_at, updated_at |
| `dbo.orders` | order_id (PK), customer_id, order_placed_at, status, currency, channel, created_at, updated_at |
| `dbo.order_items` | order_item_id (PK), order_id, line_number, product_sku, quantity, unit_price, discount_amount, created_at, updated_at |

Files: `products_vNNN.csv` (`product_sku, product_name, category, list_price, currency, is_active, updated_at`)
and payment batches `{"schema_version": "1.0", "batch_id", "generated_at", "transactions": [{transaction_id,
order_id, transaction_type, status, amount, currency, payment_method, transaction_at, refers_to_transaction_id}]}`.

## Raw: `raw.source_record`

Grain: one row per **new version** of a source record (append-only).
Columns: raw_id, entity, source_system, source_record_id, source_updated_at_utc, source_file_id,
source_file_name, file_checksum (SHA-256 of the file), record_index, payload_json (canonical JSON of the
original record), payload_checksum (SHA-256 of payload), run_id, ingested_at_utc.

## Staging: `stg.*`

Grain: one row per business key: the **latest accepted** version. Each row keeps `raw_id`,
`source_checksum` and `staged_run_id`.

| Table | Key | Normalisation |
|---|---|---|
| `stg.customer` | customer_id | trimmed text, lower-case email, full_name |
| `stg.product` | product_sku | upper-case SKU, SAR only, boolean is_active, catalog version |
| `stg.sales_order` | order_id | status mapped to pending/processing/shipped/delivered/cancelled |
| `stg.order_item` | order_item_id | quantity > 0, prices ≥ 0, 2-dp precision, discount ≤ gross |
| `stg.payment_txn` | transaction_id | type payment/refund, status succeeded/failed/pending, positive amount |

## Warehouse: `dw.*`

| Table | Grain | Keys / constraints | Update |
|---|---|---|---|
| `DimDate` | one Riyadh calendar date | PK DateKey (yyyymmdd); IsWeekend = Fri/Sat; WeekStartDate = Sunday | static |
| `DimCustomer` | one source customer | PK CustomerKey; UQ CustomerId | Type 1 |
| `DimProduct` | one SKU | PK ProductKey; UQ ProductSku; CurrentListPrice ≥ 0 | Type 1 |
| `DimOrder` | one order header | PK OrderKey; UQ OrderId; FK customer, date; status CHECK; IsCancelled persisted | Type 1 |
| `FactSalesLine` | one source order line | PK SalesLineKey; UQ OrderItemId; FKs order/date/customer/product; Quantity > 0; DiscountAmount ≤ gross; GrossAmount/NetAmount persisted | upsert by checksum |
| `FactPaymentTransaction` | one payment or refund transaction | PK PaymentTxnKey; UQ TransactionId; FKs order/customer/date; Amount > 0; NetCollectionAmount persisted | upsert by checksum |

Every warehouse row records `RowChecksum`, `InsertedRunId` and `UpdatedRunId`.

## Quality and control

| Table | Purpose |
|---|---|
| `qa.quarantine_record` | entity, source, record id, raw_id, file, run, error_code, error_reason, errors_json (all problems), original payload, status open / resolved / superseded / ignored, evaluation history |
| `qa.check_result` | per run: record, file and warehouse checks with checked / failed counts |
| `qa.reconciliation_result` | per run: rows / amounts / business comparisons with matched / explained / unresolved / informational |
| `meta.pipeline_run`, `meta.pipeline_run_stage` | durable queue, run and stage state, heartbeats, errors |
| `meta.run_entity_stats` | per run and entity: read, new versions, unchanged, duplicate, evaluated, accepted, rejected, inserted, updated, unchanged in warehouse |
| `meta.watermark` | per source table: highest `updated_at` fully published |
| `meta.source_file` | every file delivery: checksum, size, version, status, duplicate-of, message |

## Error codes

`REQUIRED_FIELD`, `INVALID_TYPE`, `INVALID_FORMAT`, `DUPLICATE_KEY`, `INVALID_QUANTITY`, `NEGATIVE_AMOUNT`,
`INVALID_AMOUNT`, `INVALID_PRECISION`, `INVALID_DISCOUNT`, `INVALID_STATUS`, `INVALID_TRANSACTION_TYPE`,
`UNSUPPORTED_CURRENCY`, `IMPOSSIBLE_TIMESTAMP`, `DATE_OUT_OF_RANGE`, `MISSING_CUSTOMER_REF`,
`MISSING_ORDER_REF`, `MISSING_PRODUCT_REF`, `INVALID_REFUND_REF`, `REFUND_EXCEEDS_COLLECTION`,
`SCHEMA_INVALID_RECORD`, and file-level `SCHEMA_INVALID_HEADER`, `SCHEMA_INVALID_JSON`,
`SCHEMA_INVALID_STRUCTURE`, `SCHEMA_INVALID_ENCODING`, `SCHEMA_INVALID_FILENAME`.
