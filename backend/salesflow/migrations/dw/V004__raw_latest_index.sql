-- "Latest raw version per record" queries (staging, reconciliation) read these columns in
-- (entity, source_record_id, raw_id DESC) order. Covering them lets SQL Server stream the
-- ROW_NUMBER() from the index without a sort, so the query needs no memory grant. This keeps
-- the pipeline working on SQL Server Express when the machine is short of memory.
DROP INDEX IX_source_record_key ON raw.source_record;
GO
CREATE INDEX IX_source_record_key ON raw.source_record (entity, source_record_id, raw_id DESC)
    INCLUDE (payload_checksum, source_file_id, source_file_name, run_id);
GO
