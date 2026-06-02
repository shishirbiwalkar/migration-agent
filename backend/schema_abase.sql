-- ============================================================
-- ABASE Legacy System Schema
-- Run this in your ABASE Supabase project (SQL Editor)
-- ============================================================

CREATE TABLE IF NOT EXISTS users (
    id                  BIGSERIAL    PRIMARY KEY,
    name                TEXT         NOT NULL UNIQUE,
    department          TEXT         NOT NULL,
    email               TEXT,
    last_login          TIMESTAMPTZ,
    active_time_minutes INTEGER      NOT NULL DEFAULT 0,
    -- Migration state tracking. Source rows are NEVER deleted before the target
    -- has committed. A row moves 'active' -> 'migrating' when its records are staged,
    -- and is hard-deleted only after GDS confirms the records are live (soft-delete safety).
    migration_status    TEXT         NOT NULL DEFAULT 'active',
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- For existing ABASE databases: add the column if the table predates this change.
ALTER TABLE users ADD COLUMN IF NOT EXISTS migration_status TEXT NOT NULL DEFAULT 'active';

CREATE TABLE IF NOT EXISTS experiments (
    id               BIGSERIAL        PRIMARY KEY,
    user_id          BIGINT           NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    plate_barcode    TEXT             NOT NULL,
    well_position    TEXT             NOT NULL,
    raw_value        DOUBLE PRECISION NOT NULL,
    compound_id      TEXT,                       -- screening compound code (e.g. CMP-A3F9C2)
    concentration_um DOUBLE PRECISION,           -- test concentration in µM
    assay_type       TEXT,                       -- Biochemical / Cell-based / Binding / ...
    recorded_at      TIMESTAMPTZ      NOT NULL,
    created_at       TIMESTAMPTZ      NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_plate_well UNIQUE (plate_barcode, well_position)
);

CREATE INDEX IF NOT EXISTS idx_experiments_user_id ON experiments (user_id);
CREATE INDEX IF NOT EXISTS idx_experiments_plate   ON experiments (plate_barcode);
