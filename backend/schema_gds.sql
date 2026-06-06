-- ============================================================
-- GDS Target System Schema
-- Run this in your GDS Supabase project (SQL Editor)
-- ============================================================
--
-- RLS NOTE: After creating all tables, run the block at the bottom of this
-- file to enable Row-Level Security on every table. This blocks public REST
-- API access (Supabase anon/authenticated roles) while leaving the backend
-- unaffected — it connects as the postgres superuser, which bypasses RLS.
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
    well_position TEXT,                               -- nullable: aggregated rows span multiple wells
    signal        DOUBLE PRECISION NOT NULL,          -- Emax (top of fitted dose-response curve)
    approved_at          TIMESTAMPTZ      NOT NULL DEFAULT NOW(),
    approved_by          TEXT,
    source_experiment_id TEXT,                        -- ABASE experiment.id (first of group) for traceability
    compound_id          TEXT,                        -- screening compound code
    concentration        DOUBLE PRECISION,            -- not used for aggregated rows
    assay_type           TEXT,                        -- Biochemical / Cell-based / Binding / ...
    -- Dose-response curve parameters (computed by migration agent via scipy Hill fitting)
    ec50_um                   DOUBLE PRECISION,       -- half-maximal effective concentration (µM)
    hill_slope                DOUBLE PRECISION,       -- Hill coefficient (steepness of the curve)
    r_squared                 DOUBLE PRECISION,       -- goodness of fit (0–1; ≥0.90 = acceptable)
    curve_quality             TEXT,                   -- excellent | good | fair | poor | failed
    num_concentration_points  INTEGER,                -- number of dilution points used in the fit
    -- one compound run per scientist globally (idempotent re-migration)
    CONSTRAINT uq_experiments_user_compound UNIQUE (gds_user_id, compound_id)
);

-- ============================================================
-- SUPABASE ALTER TABLE — run these once in the SQL Editor if
-- the table was created before v3 of this schema:
--
--   ALTER TABLE gds_experiments ALTER COLUMN well_position DROP NOT NULL;
--   ALTER TABLE gds_experiments DROP CONSTRAINT IF EXISTS uq_experiments_user_well;
--   ALTER TABLE gds_experiments ADD CONSTRAINT uq_experiments_user_compound
--       UNIQUE (gds_user_id, compound_id);
--   ALTER TABLE gds_experiments ADD COLUMN IF NOT EXISTS ec50_um DOUBLE PRECISION;
--   ALTER TABLE gds_experiments ADD COLUMN IF NOT EXISTS hill_slope DOUBLE PRECISION;
--   ALTER TABLE gds_experiments ADD COLUMN IF NOT EXISTS r_squared DOUBLE PRECISION;
--   ALTER TABLE gds_experiments ADD COLUMN IF NOT EXISTS curve_quality TEXT;
--   ALTER TABLE gds_experiments ADD COLUMN IF NOT EXISTS num_concentration_points INTEGER;
-- ============================================================

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

-- ============================================================
-- Scientist self-serve review invitations — magic-link outreach
-- One opaque token per (trace, scientist). Auto-created by the backend on first
-- use as well (app/api/respond.py), so this DDL is optional for dev.
-- ============================================================
CREATE TABLE IF NOT EXISTS review_invitations (
    id             BIGSERIAL   PRIMARY KEY,
    token          TEXT        NOT NULL UNIQUE,             -- secrets.token_urlsafe(32)
    trace_id       UUID        NOT NULL,
    scientist_name TEXT        NOT NULL,
    status         TEXT        NOT NULL DEFAULT 'notified', -- notified | replied | resolved | expired
    channel        TEXT        NOT NULL DEFAULT 'console',
    reply_text     TEXT,                                    -- the scientist's own words (audit evidence)
    sent_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    responded_at   TIMESTAMPTZ,
    expires_at     TIMESTAMPTZ NOT NULL DEFAULT (NOW() + INTERVAL '7 days'),
    CONSTRAINT uq_invite_trace_scientist UNIQUE (trace_id, scientist_name)
);

CREATE INDEX IF NOT EXISTS idx_invitations_token ON review_invitations (token);
CREATE INDEX IF NOT EXISTS idx_invitations_trace ON review_invitations (trace_id);

-- ============================================================
-- Row-Level Security
-- Blocks all public REST API access (Supabase anon / authenticated roles).
-- The backend connects as the postgres superuser → bypasses RLS, no policy needed.
-- Run this block once after all tables are created.
-- ============================================================
ALTER TABLE gds_users                  ENABLE ROW LEVEL SECURITY;
ALTER TABLE gds_staging_experiments    ENABLE ROW LEVEL SECURITY;
ALTER TABLE gds_experiments            ENABLE ROW LEVEL SECURITY;
ALTER TABLE migration_plans            ENABLE ROW LEVEL SECURITY;
ALTER TABLE migration_audit_log        ENABLE ROW LEVEL SECURITY;
ALTER TABLE migration_mappings         ENABLE ROW LEVEL SECURITY;
ALTER TABLE migration_jobs             ENABLE ROW LEVEL SECURITY;
ALTER TABLE review_invitations         ENABLE ROW LEVEL SECURITY;

CREATE INDEX IF NOT EXISTS idx_jobs_status ON migration_jobs (status);
