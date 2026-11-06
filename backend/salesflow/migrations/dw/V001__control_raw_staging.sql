-- Warehouse database: control metadata, raw landing, staging and quality schemas.
-- All *_utc columns are UTC. Business dates use Asia/Riyadh (see dw.DimDate).

CREATE SCHEMA meta;
GO
CREATE SCHEMA raw;
GO
CREATE SCHEMA stg;
GO
CREATE SCHEMA qa;
GO
CREATE SCHEMA dw;
GO

------------------------------------------------------------------------------
-- meta: durable pipeline run queue / state
------------------------------------------------------------------------------
CREATE TABLE meta.pipeline_run (
    run_id            INT IDENTITY(1,1) NOT NULL CONSTRAINT PK_pipeline_run PRIMARY KEY,
    mode              NVARCHAR(20)   NOT NULL CONSTRAINT CK_pipeline_run_mode CHECK (mode IN ('initial','incremental','reprocess')),
    status            NVARCHAR(20)   NOT NULL CONSTRAINT CK_pipeline_run_status CHECK (status IN ('queued','running','succeeded','failed')),
    requested_by      NVARCHAR(50)   NOT NULL,
    params_json       NVARCHAR(MAX)  NULL,
    retry_of_run_id   INT            NULL CONSTRAINT FK_pipeline_run_retry REFERENCES meta.pipeline_run(run_id),
    -- 1 while queued or running; a filtered unique index allows only one such run.
    active_slot       TINYINT        NULL CONSTRAINT CK_pipeline_run_slot CHECK (active_slot = 1),
    requested_at_utc  DATETIME2(3)   NOT NULL CONSTRAINT DF_pipeline_run_req DEFAULT SYSUTCDATETIME(),
    started_at_utc    DATETIME2(3)   NULL,
    finished_at_utc   DATETIME2(3)   NULL,
    heartbeat_at_utc  DATETIME2(3)   NULL,
    worker_id         NVARCHAR(100)  NULL,
    current_stage     NVARCHAR(50)   NULL,
    error_stage       NVARCHAR(50)   NULL,
    error_code        NVARCHAR(50)   NULL,
    error_message     NVARCHAR(4000) NULL,
    summary_json      NVARCHAR(MAX)  NULL,
    CONSTRAINT CK_pipeline_run_slot_state CHECK (
        (status IN ('queued','running') AND active_slot = 1) OR
        (status IN ('succeeded','failed') AND active_slot IS NULL))
);
GO
CREATE UNIQUE INDEX UX_pipeline_run_active ON meta.pipeline_run (active_slot) WHERE active_slot IS NOT NULL;
GO
CREATE INDEX IX_pipeline_run_status ON meta.pipeline_run (status, run_id);
GO

CREATE TABLE meta.pipeline_run_stage (
    run_id           INT            NOT NULL CONSTRAINT FK_run_stage_run REFERENCES meta.pipeline_run(run_id),
    stage_seq        TINYINT        NOT NULL,
    stage_name       NVARCHAR(50)   NOT NULL,
    status           NVARCHAR(20)   NOT NULL CONSTRAINT CK_run_stage_status CHECK (status IN ('pending','running','succeeded','failed','skipped')),
    started_at_utc   DATETIME2(3)   NULL,
    finished_at_utc  DATETIME2(3)   NULL,
    rows_read        INT            NULL,
    rows_written     INT            NULL,
    rows_rejected    INT            NULL,
    message          NVARCHAR(2000) NULL,
    details_json     NVARCHAR(MAX)  NULL,
    CONSTRAINT PK_pipeline_run_stage PRIMARY KEY (run_id, stage_seq)
);
GO

-- Per-entity row flow for a run (written atomically with the publish).
CREATE TABLE meta.run_entity_stats (
    run_id           INT            NOT NULL CONSTRAINT FK_run_entity_run REFERENCES meta.pipeline_run(run_id),
    entity           NVARCHAR(30)   NOT NULL,
    rows_read        INT            NOT NULL DEFAULT 0,  -- records read from source / files
    rows_new_version INT            NOT NULL DEFAULT 0,  -- new or changed raw versions landed
    rows_unchanged   INT            NOT NULL DEFAULT 0,  -- identical to latest raw version
    rows_duplicate   INT            NOT NULL DEFAULT 0,  -- duplicate deliveries / in-file duplicates
    rows_evaluated   INT            NOT NULL DEFAULT 0,  -- records validated in staging
    rows_accepted    INT            NOT NULL DEFAULT 0,
    rows_rejected    INT            NOT NULL DEFAULT 0,  -- quarantined
    rows_inserted    INT            NOT NULL DEFAULT 0,  -- warehouse inserts
    rows_updated     INT            NOT NULL DEFAULT 0,  -- warehouse updates
    rows_dw_unchanged INT           NOT NULL DEFAULT 0,  -- warehouse rows already identical
    CONSTRAINT PK_run_entity_stats PRIMARY KEY (run_id, entity)
);
GO

CREATE TABLE meta.watermark (
    source_name       NVARCHAR(50)  NOT NULL CONSTRAINT PK_watermark PRIMARY KEY,
    watermark_utc     DATETIME2(3)  NULL,   -- highest source updated_at fully processed
    last_run_id       INT           NULL,
    updated_at_utc    DATETIME2(3)  NOT NULL CONSTRAINT DF_watermark_upd DEFAULT SYSUTCDATETIME()
);
GO

-- Registry of every file delivery seen in the landing zone.
CREATE TABLE meta.source_file (
    file_id              INT IDENTITY(1,1) NOT NULL CONSTRAINT PK_source_file PRIMARY KEY,
    source_system        NVARCHAR(30)   NOT NULL CONSTRAINT CK_source_file_system CHECK (source_system IN ('product_catalog_csv','payments_json')),
    file_name            NVARCHAR(260)  NOT NULL,
    file_checksum        CHAR(64)       NOT NULL,   -- SHA-256 of file bytes
    file_size_bytes      BIGINT         NOT NULL,
    file_version         INT            NULL,       -- product snapshot version (products_vNNN.csv)
    record_count         INT            NULL,
    status               NVARCHAR(20)   NOT NULL CONSTRAINT CK_source_file_status CHECK (status IN ('landed','processed','duplicate','rejected','conflict')),
    duplicate_of_file_id INT            NULL CONSTRAINT FK_source_file_dup REFERENCES meta.source_file(file_id),
    first_seen_run_id    INT            NOT NULL CONSTRAINT FK_source_file_run REFERENCES meta.pipeline_run(run_id),
    processed_run_id     INT            NULL,
    received_at_utc      DATETIME2(3)   NOT NULL CONSTRAINT DF_source_file_recv DEFAULT SYSUTCDATETIME(),
    message              NVARCHAR(2000) NULL
);
GO
CREATE UNIQUE INDEX UX_source_file_name_checksum ON meta.source_file (file_name, file_checksum);
GO
CREATE UNIQUE INDEX UX_source_file_accepted_checksum ON meta.source_file (file_checksum) WHERE status IN ('landed','processed');
GO
CREATE UNIQUE INDEX UX_source_file_accepted_name ON meta.source_file (file_name) WHERE status IN ('landed','processed');
GO

------------------------------------------------------------------------------
-- raw: immutable, append-only source payloads (one row per new record version)
------------------------------------------------------------------------------
CREATE TABLE raw.source_record (
    raw_id                BIGINT IDENTITY(1,1) NOT NULL CONSTRAINT PK_source_record PRIMARY KEY,
    entity                NVARCHAR(30)   NOT NULL,   -- customer | order | order_item | product | payment_txn
    source_system         NVARCHAR(30)   NOT NULL,   -- ops_sql | product_catalog_csv | payments_json
    source_record_id      NVARCHAR(100)  NOT NULL,   -- business key in the source
    source_updated_at_utc DATETIME2(3)   NULL,       -- source modification timestamp (SQL source)
    source_file_id        INT            NULL CONSTRAINT FK_source_record_file REFERENCES meta.source_file(file_id),
    source_file_name      NVARCHAR(260)  NULL,
    file_checksum         CHAR(64)       NULL,
    record_index          INT            NULL,       -- row / array position inside the file
    payload_json          NVARCHAR(MAX)  NOT NULL,   -- original record, canonical JSON
    payload_checksum      CHAR(64)       NOT NULL,   -- SHA-256 of payload_json (change detection)
    run_id                INT            NOT NULL CONSTRAINT FK_source_record_run REFERENCES meta.pipeline_run(run_id),
    ingested_at_utc       DATETIME2(3)   NOT NULL CONSTRAINT DF_source_record_ing DEFAULT SYSUTCDATETIME()
);
GO
CREATE INDEX IX_source_record_key ON raw.source_record (entity, source_record_id, raw_id DESC) INCLUDE (payload_checksum, source_file_id);
GO
CREATE INDEX IX_source_record_run ON raw.source_record (run_id, entity);
GO
CREATE INDEX IX_source_record_file ON raw.source_record (source_file_id, source_record_id) INCLUDE (payload_checksum);
GO

------------------------------------------------------------------------------
-- stg: typed, normalised, accepted current state per business key
------------------------------------------------------------------------------
CREATE TABLE stg.customer (
    customer_id        INT            NOT NULL CONSTRAINT PK_stg_customer PRIMARY KEY,
    customer_code      NVARCHAR(20)   NOT NULL,
    first_name         NVARCHAR(100)  NULL,
    last_name          NVARCHAR(100)  NULL,
    full_name          NVARCHAR(201)  NOT NULL,
    email              NVARCHAR(200)  NOT NULL,
    city               NVARCHAR(100)  NULL,
    created_at_utc     DATETIME2(3)   NOT NULL,
    updated_at_utc     DATETIME2(3)   NOT NULL,
    raw_id             BIGINT         NOT NULL,
    source_checksum    CHAR(64)       NOT NULL,
    staged_run_id      INT            NOT NULL
);
GO
CREATE TABLE stg.product (
    product_sku        NVARCHAR(40)   NOT NULL CONSTRAINT PK_stg_product PRIMARY KEY,
    product_name       NVARCHAR(200)  NOT NULL,
    category           NVARCHAR(100)  NOT NULL,
    list_price         DECIMAL(18,2)  NOT NULL,
    currency           CHAR(3)        NOT NULL,
    is_active          BIT            NOT NULL,
    source_updated_at_utc DATETIME2(3) NULL,
    source_file_id     INT            NOT NULL,
    source_file_version INT           NOT NULL,
    raw_id             BIGINT         NOT NULL,
    source_checksum    CHAR(64)       NOT NULL,
    staged_run_id      INT            NOT NULL
);
GO
CREATE TABLE stg.sales_order (
    order_id           INT            NOT NULL CONSTRAINT PK_stg_sales_order PRIMARY KEY,
    customer_id        INT            NOT NULL,
    order_placed_at_utc DATETIME2(3)  NOT NULL,
    order_status       NVARCHAR(20)   NOT NULL,
    currency           CHAR(3)        NOT NULL,
    channel            NVARCHAR(20)   NULL,
    created_at_utc     DATETIME2(3)   NOT NULL,
    updated_at_utc     DATETIME2(3)   NOT NULL,
    raw_id             BIGINT         NOT NULL,
    source_checksum    CHAR(64)       NOT NULL,
    staged_run_id      INT            NOT NULL
);
GO
CREATE TABLE stg.order_item (
    order_item_id      INT            NOT NULL CONSTRAINT PK_stg_order_item PRIMARY KEY,
    order_id           INT            NOT NULL,
    line_number        INT            NOT NULL,
    product_sku        NVARCHAR(40)   NOT NULL,
    quantity           INT            NOT NULL,
    unit_price         DECIMAL(18,2)  NOT NULL,
    discount_amount    DECIMAL(18,2)  NOT NULL,
    created_at_utc     DATETIME2(3)   NOT NULL,
    updated_at_utc     DATETIME2(3)   NOT NULL,
    raw_id             BIGINT         NOT NULL,
    source_checksum    CHAR(64)       NOT NULL,
    staged_run_id      INT            NOT NULL
);
GO
CREATE INDEX IX_stg_order_item_order ON stg.order_item (order_id);
GO
CREATE TABLE stg.payment_txn (
    transaction_id     NVARCHAR(40)   NOT NULL CONSTRAINT PK_stg_payment_txn PRIMARY KEY,
    order_id           INT            NOT NULL,
    transaction_type   NVARCHAR(10)   NOT NULL,
    transaction_status NVARCHAR(12)   NOT NULL,
    amount             DECIMAL(18,2)  NOT NULL,   -- always positive; direction from transaction_type
    currency           CHAR(3)        NOT NULL,
    payment_method     NVARCHAR(30)   NULL,
    transaction_at_utc DATETIME2(3)   NOT NULL,
    refers_to_transaction_id NVARCHAR(40) NULL,
    source_file_id     INT            NOT NULL,
    raw_id             BIGINT         NOT NULL,
    source_checksum    CHAR(64)       NOT NULL,
    staged_run_id      INT            NOT NULL
);
GO
CREATE INDEX IX_stg_payment_txn_order ON stg.payment_txn (order_id);
GO
CREATE INDEX IX_stg_payment_txn_refers ON stg.payment_txn (refers_to_transaction_id) WHERE refers_to_transaction_id IS NOT NULL;
GO

------------------------------------------------------------------------------
-- qa: quarantine, check results, reconciliation
------------------------------------------------------------------------------
CREATE TABLE qa.quarantine_record (
    quarantine_id        BIGINT IDENTITY(1,1) NOT NULL CONSTRAINT PK_quarantine_record PRIMARY KEY,
    entity               NVARCHAR(30)   NOT NULL,   -- record entity, or 'file' for file-level rejections
    source_system        NVARCHAR(30)   NOT NULL,
    source_record_id     NVARCHAR(100)  NOT NULL,
    raw_id               BIGINT         NULL,
    source_file_id       INT            NULL,
    source_file_name     NVARCHAR(260)  NULL,
    run_id               INT            NOT NULL CONSTRAINT FK_quarantine_run REFERENCES meta.pipeline_run(run_id),
    error_code           NVARCHAR(50)   NOT NULL,   -- primary (first) error
    error_reason         NVARCHAR(1000) NOT NULL,
    errors_json          NVARCHAR(MAX)  NOT NULL,   -- all errors found for the record
    payload              NVARCHAR(MAX)  NOT NULL,   -- original payload (file preview for file-level)
    payload_checksum     CHAR(64)       NULL,
    status               NVARCHAR(20)   NOT NULL CONSTRAINT CK_quarantine_status CHECK (status IN ('open','resolved','superseded','ignored')),
    evaluation_count     INT            NOT NULL CONSTRAINT DF_quarantine_eval DEFAULT 1,
    last_evaluated_run_id INT           NOT NULL,
    resolved_run_id      INT            NULL,
    status_note          NVARCHAR(500)  NULL,
    created_at_utc       DATETIME2(3)   NOT NULL CONSTRAINT DF_quarantine_created DEFAULT SYSUTCDATETIME(),
    updated_at_utc       DATETIME2(3)   NOT NULL CONSTRAINT DF_quarantine_updated DEFAULT SYSUTCDATETIME()
);
GO
CREATE INDEX IX_quarantine_status ON qa.quarantine_record (status, error_code);
GO
CREATE INDEX IX_quarantine_key ON qa.quarantine_record (entity, source_record_id, status);
GO
CREATE INDEX IX_quarantine_raw ON qa.quarantine_record (raw_id) WHERE raw_id IS NOT NULL;
GO

CREATE TABLE qa.check_result (
    check_result_id   BIGINT IDENTITY(1,1) NOT NULL CONSTRAINT PK_check_result PRIMARY KEY,
    run_id            INT            NOT NULL CONSTRAINT FK_check_run REFERENCES meta.pipeline_run(run_id),
    check_code        NVARCHAR(60)   NOT NULL,
    check_name        NVARCHAR(200)  NOT NULL,
    check_level       NVARCHAR(20)   NOT NULL CONSTRAINT CK_check_level CHECK (check_level IN ('record','file','warehouse')),
    entity            NVARCHAR(30)   NOT NULL,
    severity          NVARCHAR(10)   NOT NULL CONSTRAINT CK_check_sev CHECK (severity IN ('error','warning')),
    status            NVARCHAR(10)   NOT NULL CONSTRAINT CK_check_status CHECK (status IN ('passed','failed')),
    checked_count     INT            NOT NULL,
    failed_count      INT            NOT NULL,
    details           NVARCHAR(2000) NULL
);
GO
CREATE INDEX IX_check_result_run ON qa.check_result (run_id);
GO

CREATE TABLE qa.reconciliation_result (
    recon_id              BIGINT IDENTITY(1,1) NOT NULL CONSTRAINT PK_recon PRIMARY KEY,
    run_id                INT            NOT NULL CONSTRAINT FK_recon_run REFERENCES meta.pipeline_run(run_id),
    seq                   SMALLINT       NOT NULL,
    area                  NVARCHAR(20)   NOT NULL CONSTRAINT CK_recon_area CHECK (area IN ('rows','amounts','business')),
    entity                NVARCHAR(30)   NOT NULL,
    metric                NVARCHAR(100)  NOT NULL,
    source_label          NVARCHAR(200)  NOT NULL,
    warehouse_label       NVARCHAR(200)  NOT NULL,
    source_value          DECIMAL(20,2)  NOT NULL,
    warehouse_value       DECIMAL(20,2)  NOT NULL,
    difference            DECIMAL(20,2)  NOT NULL,
    explained_difference  DECIMAL(20,2)  NOT NULL,
    unexplained_difference DECIMAL(20,2) NOT NULL,
    state                 NVARCHAR(20)   NOT NULL CONSTRAINT CK_recon_state CHECK (state IN ('matched','explained','unresolved','informational')),
    explanation           NVARCHAR(2000) NULL,
    details_json          NVARCHAR(MAX)  NULL
);
GO
CREATE INDEX IX_recon_run ON qa.reconciliation_result (run_id, seq);
GO
