-- Run in the application's SQL Server database.
-- Existing tables are preserved. Text metadata uses Unicode-capable NVARCHAR.
IF OBJECT_ID(N'dbo.auth_users', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.auth_users (
        id UNIQUEIDENTIFIER NOT NULL PRIMARY KEY,
        username VARCHAR(64) COLLATE Latin1_General_100_BIN2 NOT NULL UNIQUE,
        password_hash VARCHAR(256) NOT NULL,
        is_active BIT NOT NULL DEFAULT 1,
        failed_attempts INT NOT NULL DEFAULT 0,
        locked_until DATETIME2 NULL,
        created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
    );
END;
IF OBJECT_ID(N'dbo.auth_sessions', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.auth_sessions (
        token_hash BINARY(32) NOT NULL PRIMARY KEY,
        user_id UNIQUEIDENTIFIER NOT NULL REFERENCES dbo.auth_users(id) ON DELETE CASCADE,
        expires_at DATETIME2 NOT NULL,
        remember_me BIT NOT NULL CONSTRAINT DF_auth_sessions_remember_me DEFAULT 0
    );
    CREATE INDEX IX_auth_sessions_expiry ON dbo.auth_sessions (expires_at);
END;
IF COL_LENGTH(N'dbo.auth_sessions', N'remember_me') IS NULL
BEGIN
    ALTER TABLE dbo.auth_sessions ADD remember_me BIT NOT NULL
        CONSTRAINT DF_auth_sessions_remember_me DEFAULT 0 WITH VALUES;
END;
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
IF OBJECT_ID(N'dbo.recognition_burst_frames', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.recognition_burst_frames (
        record_id UNIQUEIDENTIFIER NOT NULL
            REFERENCES dbo.recognition_records(id) ON DELETE CASCADE,
        frame_index TINYINT NOT NULL CHECK (frame_index BETWEEN 1 AND 9),
        duration_seconds FLOAT NOT NULL CHECK (duration_seconds >= 1.5 AND duration_seconds <= 2.0),
        evidence VARBINARY(MAX) NOT NULL,
        PRIMARY KEY (record_id, frame_index)
    );
END;
IF OBJECT_ID(N'dbo.recognition_baselines', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.recognition_baselines (
        id BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        record_id UNIQUEIDENTIFIER NOT NULL REFERENCES dbo.recognition_records(id),
        region NVARCHAR(20) NOT NULL,
        shop_code NVARCHAR(50) NOT NULL,
        camera_name NVARCHAR(100) NOT NULL,
        approved_at DATETIME2 NOT NULL
    );
    CREATE INDEX IX_recognition_baseline_camera
        ON dbo.recognition_baselines (region, shop_code, camera_name, id DESC);
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
