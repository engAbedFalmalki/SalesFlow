-- Dimensional warehouse model. See docs/data-dictionary.md for grains.
-- Customer, product and order descriptive attributes are Type 1 (overwritten).
-- Money columns are DECIMAL; no FLOAT/REAL anywhere.

-- Grain: one row per Asia/Riyadh calendar date.
CREATE TABLE dw.DimDate (
    DateKey        INT          NOT NULL CONSTRAINT PK_DimDate PRIMARY KEY,   -- yyyymmdd
    [Date]         DATE         NOT NULL CONSTRAINT UQ_DimDate_Date UNIQUE,
    [Year]         SMALLINT     NOT NULL,
    [Quarter]      TINYINT      NOT NULL,
    [Month]        TINYINT      NOT NULL,
    MonthName      NVARCHAR(10) NOT NULL,
    DayOfMonth     TINYINT      NOT NULL,
    IsoDayOfWeek   TINYINT      NOT NULL,   -- 1 = Monday ... 7 = Sunday
    DayName        NVARCHAR(10) NOT NULL,
    IsWeekend      BIT          NOT NULL,   -- Friday and Saturday in Saudi Arabia
    WeekStartDate  DATE         NOT NULL,   -- week starts Sunday
    MonthStartDate DATE         NOT NULL
);
GO

-- Grain: one row per source customer (customer_id). Type 1.
CREATE TABLE dw.DimCustomer (
    CustomerKey          INT IDENTITY(1,1) NOT NULL CONSTRAINT PK_DimCustomer PRIMARY KEY,
    CustomerId           INT            NOT NULL CONSTRAINT UQ_DimCustomer_Id UNIQUE,
    CustomerCode         NVARCHAR(20)   NOT NULL,
    FullName             NVARCHAR(201)  NOT NULL,
    Email                NVARCHAR(200)  NOT NULL,
    City                 NVARCHAR(100)  NULL,
    CustomerCreatedAtUtc DATETIME2(3)   NOT NULL,
    SourceUpdatedAtUtc   DATETIME2(3)   NOT NULL,
    RowChecksum          BINARY(32)     NOT NULL,
    InsertedRunId        INT            NOT NULL,
    UpdatedRunId         INT            NOT NULL
);
GO

-- Grain: one row per product SKU. Type 1: CurrentListPrice and Category hold the
-- latest catalog values; historical sales use FactSalesLine.UnitPrice instead.
CREATE TABLE dw.DimProduct (
    ProductKey           INT IDENTITY(1,1) NOT NULL CONSTRAINT PK_DimProduct PRIMARY KEY,
    ProductSku           NVARCHAR(40)   NOT NULL CONSTRAINT UQ_DimProduct_Sku UNIQUE,
    ProductName          NVARCHAR(200)  NOT NULL,
    Category             NVARCHAR(100)  NOT NULL,
    CurrentListPrice     DECIMAL(18,2)  NOT NULL CONSTRAINT CK_DimProduct_Price CHECK (CurrentListPrice >= 0),
    IsActive             BIT            NOT NULL,
    CatalogVersion       INT            NOT NULL,
    RowChecksum          BINARY(32)     NOT NULL,
    InsertedRunId        INT            NOT NULL,
    UpdatedRunId         INT            NOT NULL
);
GO
CREATE INDEX IX_DimProduct_Category ON dw.DimProduct (Category);
GO

-- Grain: one row per source order (order header). Type 1 (status overwritten).
CREATE TABLE dw.DimOrder (
    OrderKey             INT IDENTITY(1,1) NOT NULL CONSTRAINT PK_DimOrder PRIMARY KEY,
    OrderId              INT            NOT NULL CONSTRAINT UQ_DimOrder_Id UNIQUE,
    CustomerKey          INT            NOT NULL CONSTRAINT FK_DimOrder_Customer REFERENCES dw.DimCustomer(CustomerKey),
    OrderPlacedAtUtc     DATETIME2(3)   NOT NULL,
    OrderDateKey         INT            NOT NULL CONSTRAINT FK_DimOrder_Date REFERENCES dw.DimDate(DateKey),
    OrderStatus          NVARCHAR(20)   NOT NULL CONSTRAINT CK_DimOrder_Status CHECK (OrderStatus IN ('pending','processing','shipped','delivered','cancelled')),
    IsCancelled          AS CAST(CASE WHEN OrderStatus = 'cancelled' THEN 1 ELSE 0 END AS BIT) PERSISTED,
    Channel              NVARCHAR(20)   NULL,
    SourceUpdatedAtUtc   DATETIME2(3)   NOT NULL,
    RowChecksum          BINARY(32)     NOT NULL,
    InsertedRunId        INT            NOT NULL,
    UpdatedRunId         INT            NOT NULL
);
GO
CREATE INDEX IX_DimOrder_Date ON dw.DimOrder (OrderDateKey) INCLUDE (OrderStatus, CustomerKey);
GO

-- Grain: one row per source order line (order_item_id).
CREATE TABLE dw.FactSalesLine (
    SalesLineKey     BIGINT IDENTITY(1,1) NOT NULL CONSTRAINT PK_FactSalesLine PRIMARY KEY,
    OrderItemId      INT            NOT NULL CONSTRAINT UQ_FactSalesLine_Item UNIQUE,
    OrderKey         INT            NOT NULL CONSTRAINT FK_FactSalesLine_Order REFERENCES dw.DimOrder(OrderKey),
    OrderDateKey     INT            NOT NULL CONSTRAINT FK_FactSalesLine_Date REFERENCES dw.DimDate(DateKey),
    CustomerKey      INT            NOT NULL CONSTRAINT FK_FactSalesLine_Customer REFERENCES dw.DimCustomer(CustomerKey),
    ProductKey       INT            NOT NULL CONSTRAINT FK_FactSalesLine_Product REFERENCES dw.DimProduct(ProductKey),
    LineNumber       INT            NOT NULL,
    Quantity         INT            NOT NULL CONSTRAINT CK_FactSalesLine_Qty CHECK (Quantity > 0),
    UnitPrice        DECIMAL(18,2)  NOT NULL CONSTRAINT CK_FactSalesLine_Price CHECK (UnitPrice >= 0),  -- historical, from the order line
    DiscountAmount   DECIMAL(18,2)  NOT NULL CONSTRAINT CK_FactSalesLine_Disc CHECK (DiscountAmount >= 0),
    GrossAmount      AS CAST(Quantity * UnitPrice AS DECIMAL(18,2)) PERSISTED,
    NetAmount        AS CAST(Quantity * UnitPrice - DiscountAmount AS DECIMAL(18,2)) PERSISTED,
    RowChecksum      BINARY(32)     NOT NULL,
    InsertedRunId    INT            NOT NULL,
    UpdatedRunId     INT            NOT NULL,
    CONSTRAINT CK_FactSalesLine_DiscLeGross CHECK (DiscountAmount <= Quantity * UnitPrice)
);
GO
CREATE INDEX IX_FactSalesLine_Date ON dw.FactSalesLine (OrderDateKey) INCLUDE (OrderKey, ProductKey, Quantity, UnitPrice, DiscountAmount);
GO
CREATE INDEX IX_FactSalesLine_Order ON dw.FactSalesLine (OrderKey);
GO
CREATE INDEX IX_FactSalesLine_Product ON dw.FactSalesLine (ProductKey, OrderDateKey);
GO

-- Grain: one row per source payment or refund transaction (transaction_id).
-- Amount is always positive; TransactionType gives the direction.
CREATE TABLE dw.FactPaymentTransaction (
    PaymentTxnKey         BIGINT IDENTITY(1,1) NOT NULL CONSTRAINT PK_FactPaymentTransaction PRIMARY KEY,
    TransactionId         NVARCHAR(40)   NOT NULL CONSTRAINT UQ_FactPayment_Txn UNIQUE,
    OrderKey              INT            NOT NULL CONSTRAINT FK_FactPayment_Order REFERENCES dw.DimOrder(OrderKey),
    CustomerKey           INT            NOT NULL CONSTRAINT FK_FactPayment_Customer REFERENCES dw.DimCustomer(CustomerKey),
    TransactionDateKey    INT            NOT NULL CONSTRAINT FK_FactPayment_Date REFERENCES dw.DimDate(DateKey),
    TransactionAtUtc      DATETIME2(3)   NOT NULL,
    TransactionType       NVARCHAR(10)   NOT NULL CONSTRAINT CK_FactPayment_Type CHECK (TransactionType IN ('payment','refund')),
    TransactionStatus     NVARCHAR(12)   NOT NULL CONSTRAINT CK_FactPayment_Status CHECK (TransactionStatus IN ('succeeded','failed','pending')),
    Amount                DECIMAL(18,2)  NOT NULL CONSTRAINT CK_FactPayment_Amount CHECK (Amount > 0),
    PaymentMethod         NVARCHAR(30)   NULL,
    RefersToTransactionId NVARCHAR(40)   NULL,
    -- +Amount for a successful payment, -Amount for a successful refund, 0 otherwise.
    NetCollectionAmount   AS CAST(CASE WHEN TransactionStatus = 'succeeded'
                                       THEN CASE WHEN TransactionType = 'payment' THEN Amount ELSE -Amount END
                                       ELSE 0 END AS DECIMAL(18,2)) PERSISTED,
    SourceFileId          INT            NOT NULL,
    RowChecksum           BINARY(32)     NOT NULL,
    InsertedRunId         INT            NOT NULL,
    UpdatedRunId          INT            NOT NULL
);
GO
CREATE INDEX IX_FactPayment_Date ON dw.FactPaymentTransaction (TransactionDateKey) INCLUDE (TransactionType, TransactionStatus, Amount);
GO
CREATE INDEX IX_FactPayment_Order ON dw.FactPaymentTransaction (OrderKey);
GO

-- Populate DimDate for 2025-01-01 .. 2027-12-31 (business dates, Asia/Riyadh).
WITH n AS (
    SELECT TOP (DATEDIFF(DAY, '2025-01-01', '2027-12-31') + 1)
           ROW_NUMBER() OVER (ORDER BY (SELECT NULL)) - 1 AS i
    FROM sys.all_objects a CROSS JOIN sys.all_objects b
), d AS (
    SELECT CAST(DATEADD(DAY, i, '2025-01-01') AS DATE) AS dt FROM n
), x AS (
    SELECT dt, (DATEDIFF(DAY, '19000101', dt) % 7) + 1 AS iso FROM d
)
INSERT INTO dw.DimDate (DateKey, [Date], [Year], [Quarter], [Month], MonthName, DayOfMonth,
                        IsoDayOfWeek, DayName, IsWeekend, WeekStartDate, MonthStartDate)
SELECT YEAR(dt) * 10000 + MONTH(dt) * 100 + DAY(dt),
       dt, YEAR(dt), DATEPART(QUARTER, dt), MONTH(dt),
       LEFT(DATENAME(MONTH, dt), 10), DAY(dt), iso,
       CHOOSE(iso, N'Monday', N'Tuesday', N'Wednesday', N'Thursday', N'Friday', N'Saturday', N'Sunday'),
       CASE WHEN iso IN (5, 6) THEN 1 ELSE 0 END,
       DATEADD(DAY, -(iso % 7), dt),
       DATEFROMPARTS(YEAR(dt), MONTH(dt), 1)
FROM x;
GO
