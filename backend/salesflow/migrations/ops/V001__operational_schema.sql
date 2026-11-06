-- Operational (source) database for the simulated e-commerce application.
--
-- This schema deliberately mirrors a permissive legacy application database:
-- there are no foreign keys or CHECK constraints on business values, so dirty
-- rows (invalid quantities, unknown products, bad statuses) can exist in the
-- source and must be caught by the pipeline. All timestamps are stored in UTC.

CREATE TABLE dbo.customers (
    customer_id     INT            NOT NULL CONSTRAINT PK_customers PRIMARY KEY,
    customer_code   NVARCHAR(20)   NOT NULL,
    first_name      NVARCHAR(100)  NULL,
    last_name       NVARCHAR(100)  NULL,
    email           NVARCHAR(200)  NULL,
    city            NVARCHAR(100)  NULL,
    created_at      DATETIME2(3)   NOT NULL,   -- UTC
    updated_at      DATETIME2(3)   NOT NULL    -- UTC, modification timestamp
);
GO
CREATE INDEX IX_customers_updated_at ON dbo.customers (updated_at, customer_id);
GO

CREATE TABLE dbo.orders (
    order_id         INT            NOT NULL CONSTRAINT PK_orders PRIMARY KEY,
    customer_id      INT            NULL,
    order_placed_at  DATETIME2(3)   NULL,      -- UTC
    status           NVARCHAR(30)   NULL,
    currency         NVARCHAR(10)   NULL,
    channel          NVARCHAR(20)   NULL,
    created_at       DATETIME2(3)   NOT NULL,  -- UTC
    updated_at       DATETIME2(3)   NOT NULL   -- UTC, modification timestamp
);
GO
CREATE INDEX IX_orders_updated_at ON dbo.orders (updated_at, order_id);
GO

CREATE TABLE dbo.order_items (
    order_item_id    INT            NOT NULL CONSTRAINT PK_order_items PRIMARY KEY,
    order_id         INT            NULL,
    line_number      INT            NULL,
    product_sku      NVARCHAR(40)   NULL,
    quantity         INT            NULL,
    unit_price       DECIMAL(12,2)  NULL,      -- price recorded at order time
    discount_amount  DECIMAL(12,2)  NULL,      -- line-level discount (SAR)
    created_at       DATETIME2(3)   NOT NULL,  -- UTC
    updated_at       DATETIME2(3)   NOT NULL   -- UTC, modification timestamp
);
GO
CREATE INDEX IX_order_items_updated_at ON dbo.order_items (updated_at, order_item_id);
GO
CREATE INDEX IX_order_items_order ON dbo.order_items (order_id);
GO
