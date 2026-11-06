-- Arabic versions of human-readable pipeline messages (the UI can be switched to Arabic).
ALTER TABLE qa.quarantine_record ADD error_reason_ar NVARCHAR(1000) NULL;
GO
ALTER TABLE meta.source_file ADD message_ar NVARCHAR(2000) NULL;
GO
