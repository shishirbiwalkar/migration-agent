-- ============================================================
-- GDS Target System Schema
-- Run this in your GDS Supabase project (SQL Editor)
-- ============================================================

CREATE TABLE IF NOT EXISTS gds_users (
    gds_user_id UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT        NOT NULL UNIQUE,
    role        TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- UNLOGGED = fast transient buffer; data lost on crash (intentional for staging)
-- data JSONB holds any migration's columns — generic, not ABASE-specific
CREATE UNLOGGED TABLE IF NOT EXISTS gds_staging_experiments (
    staging_id  BIGSERIAL   PRIMARY KEY,
    trace_id    UUID        NOT NULL,
    data        JSONB       NOT NULL,
    risk_level  TEXT        NOT NULL DEFAULT 'auto',
    status      TEXT        NOT NULL DEFAULT 'pending',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_staging_trace_id    ON gds_staging_experiments (trace_id);
CREATE INDEX IF NOT EXISTS idx_staging_status      ON gds_staging_experiments (status);
CREATE INDEX IF NOT EXISTS idx_staging_risk_level  ON gds_staging_experiments (risk_level);

CREATE TABLE IF NOT EXISTS gds_experiments (
    experiment_id UUID             PRIMARY KEY DEFAULT gen_random_uuid(),
    gds_user_id   UUID             NOT NULL REFERENCES gds_users(gds_user_id),
    trace_id      UUID             NOT NULL,
    well_position TEXT             NOT NULL,
    signal        DOUBLE PRECISION NOT NULL,
    approved_at          TIMESTAMPTZ      NOT NULL DEFAULT NOW(),
    approved_by          TEXT,
    source_experiment_id TEXT,                        -- ABASE experiment.id for traceability
    compound_id          TEXT,                        -- screening compound code
    concentration        DOUBLE PRECISION,            -- test concentration (µM)
    assay_type           TEXT,                        -- Biochemical / Cell-based / Binding / ...
    -- one well per scientist globally (idempotent re-migration)
    CONSTRAINT uq_experiments_user_well UNIQUE (gds_user_id, well_position)
);

CREATE INDEX IF NOT EXISTS idx_experiments_trace_id ON gds_experiments (trace_id);
CREATE INDEX IF NOT EXISTS idx_experiments_user_id  ON gds_experiments (gds_user_id);

CREATE TABLE IF NOT EXISTS migration_plans (
    id         BIGSERIAL   PRIMARY KEY,
    trace_id     UUID        NOT NULL,
    plan_json    JSONB       NOT NULL,
    initiated_by TEXT        DEFAULT 'System',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_plans_trace UNIQUE (trace_id)
);

CREATE TABLE IF NOT EXISTS migration_audit_log (
    id              BIGSERIAL   PRIMARY KEY,
    trace_id        UUID        NOT NULL,
    event           TEXT        NOT NULL,  -- auto_approved | hitl_approved | hitl_rejected | rolled_back | reviewer_resolved | error
    staged_count    INTEGER,
    promoted_count  INTEGER,
    excluded_count  INTEGER,
    approved_by     TEXT,
    error_detail    TEXT,
    schema_mapping  JSONB,
    reasoning_trace JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
    -- no UNIQUE constraint — append-only, one row per event per trace
);

CREATE INDEX IF NOT EXISTS idx_audit_trace_id ON migration_audit_log (trace_id);
CREATE INDEX IF NOT EXISTS idx_audit_event    ON migration_audit_log (event);

-- ============================================================
-- Mapping reuse / cache  — learned mappings, replayed deterministically
-- Keyed by a fingerprint of the (source, target) schema pair. A cache hit lets
-- the pipeline skip the LLM/agent entirely and replay the stored transform.
-- ============================================================
CREATE TABLE IF NOT EXISTS migration_mappings (
    schema_fingerprint TEXT        PRIMARY KEY,   -- hash(source_fp : target_fp)
    source_fingerprint TEXT        NOT NULL,
    target_fingerprint TEXT        NOT NULL,
    transform_script   TEXT        NOT NULL,       -- the pandas transform to replay
    promotion_config   JSONB       NOT NULL,
    derived_trace_id   UUID,                        -- the run that first learned this mapping
    hit_count          INTEGER     NOT NULL DEFAULT 0,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_used_at       TIMESTAMPTZ
);

-- ============================================================
-- Async job tracking — background migration runs + status polling
-- ============================================================
CREATE TABLE IF NOT EXISTS migration_jobs (
    trace_id       UUID        PRIMARY KEY,
    status         TEXT        NOT NULL DEFAULT 'queued',  -- queued | running | succeeded | failed
    mapping_source TEXT,                                    -- agent | cache (set once known)
    result         JSONB,
    error          TEXT,
    initiated_by   TEXT        DEFAULT 'System',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_jobs_status ON migration_jobs (status);
