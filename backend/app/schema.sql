-- Run in the application's SQL Server database.
-- Existing tables are preserved. Text metadata uses Unicode-capable NVARCHAR.
IF OBJECT_ID(N'dbo.recognition_records', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.recognition_records (
        id UNIQUEIDENTIFIER NOT NULL PRIMARY KEY,
        shop_code NVARCHAR(50) NOT NULL,
        camera_name NVARCHAR(100) NOT NULL,
        region NVARCHAR(20) NOT NULL
            CHECK (region IN ('Hong Kong', 'Macau')),
        filename NVARCHAR(255) NOT NULL,
        uploaded_at DATETIME2 NOT NULL,
        captured_at DATETIME2 NULL,
        width INT NOT NULL CHECK (width > 0),
        height INT NOT NULL CHECK (height > 0),
        outcome VARCHAR(20) NOT NULL CHECK (outcome IN ('flagged', 'needs_review')),
        review_status VARCHAR(20) NOT NULL DEFAULT 'new'
            CHECK (review_status IN ('new', 'acknowledged', 'false_positive')),
        review_note NVARCHAR(1000) NOT NULL DEFAULT '',
        reviewed_at DATETIME2 NULL,
        analyzer_version VARCHAR(50) NOT NULL,
        evidence VARBINARY(MAX) NOT NULL
    );
    CREATE INDEX IX_recognition_uploaded
        ON dbo.recognition_records (uploaded_at DESC, id);
    CREATE INDEX IX_recognition_review
        ON dbo.recognition_records (review_status, outcome);
END;
IF OBJECT_ID(N'dbo.recognition_checks', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.recognition_checks (
        record_id UNIQUEIDENTIFIER NOT NULL
            REFERENCES dbo.recognition_records(id) ON DELETE CASCADE,
        category VARCHAR(20) NOT NULL CHECK (category IN ('signal', 'timestamp', 'scene')),
        status VARCHAR(20) NOT NULL CHECK (status IN ('pass', 'flagged', 'not_assessed')),
        explanation NVARCHAR(1000) NOT NULL,
        PRIMARY KEY (record_id, category)
    );
END;
