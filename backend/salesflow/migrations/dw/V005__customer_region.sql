-- Customer region (one of Saudi Arabia's 13 administrative regions), derived from the customer's
-- city by the load step using the reference in salesflow/geo.py. NULL when the city is unknown.
-- Existing rows are filled by the next pipeline run.
ALTER TABLE dw.DimCustomer ADD Region NVARCHAR(60) NULL;
GO
CREATE INDEX IX_DimCustomer_Region_City ON dw.DimCustomer (Region, City);
GO
