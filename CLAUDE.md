# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Migration Agent** is an AI-driven, read-only data migration system that discovers source/target database schemas at runtime, reasons about column mapping, generates pandas+scipy transformation scripts, fits Hill dose-response curves, classifies experiments by R² goodness-of-fit, and stages results for human-in-the-loop review.

**Core principle:** Agents are purely computational and never write to any database. All DB writes are handled by deterministic infrastructure code, leaving a full audit trail.

## Architecture

### Dual-Database Setup
- **ABASE** (Supabase us-west-2): Source system with `users` and `experiments` tables
- **GDS** (Supabase us-east-2): Target system with `gds_users`, `gds_staging_experiments`, `gds_experiments` tables
- Both URLs are stored in `.env` with percent-encoded special characters (password contains `@` encoded as `%40`)

### Key Components

#### Backend (Python + FastAPI)
- **`app/main.py`** — FastAPI app, dual pool lifespan, CORS, trace ID middleware
- **`app/agents/migration_agent.py`** — Core migration agent — truly generic, read-only
  - Accepts `source_url` and `target_url` (works with ANY PostgreSQL databases)
  - Discovers schemas via `information_schema` at runtime
  - Samples real rows, transforms in-memory with pandas + numpy + scipy
  - When concentration columns are detected: groups wells by (scientist, compound), normalizes RFU → % inhibition using plate controls, fits Hill equation (scipy curve_fit) to compute EC50/Hill slope/R²/Emax, classifies each compound R² ≥ 0.90 → `auto`, R² < 0.90 → `review`
  - Returns `cleaned_records` (one row per compound) + `promotion_config` JSON (schema mapping)
  - Tool self-repair loop: on script error, agent reads traceback and rewrites
  - Per-run state via `_RunState` dataclass (no module-level globals)
- **`app/agents/critic_agent.py`** — Single-shot LLM review of `promotion_config` before any data moves
  - Returns `APPROVE` / `FLAG` verdict with per-finding severity (`error` / `warning` / `info`)
  - **Severity-based escalation:** only a `FLAG` *with at least one `error`-severity finding* forces every row to mandatory HITL. Warnings/info are surfaced in the UI but never block auto-promotion
  - Fail-open: if Gemini is unavailable, pipeline proceeds (critic unavailability ≠ data risk)
- **`app/agents/review_agent.py`** — Review Resolution Agent (agentic, tool-calling loop)
  - Translates a researcher's plain-English message into approve/exclude/reject decisions
  - 5 tools: `get_pending_wells`, `approve_well`, `exclude_well`, `approve_all_wells`, `exclude_all_wells`
  - Writes attributed audit events (actor + timestamp)
- **`app/agents/verification_agent.py`** — Verification Agent (agentic, 8 read-only tools)
  - Mandatory battery: reconciliation, independent R²-threshold re-check, staging vs production comparison
  - Produces an enterprise-grade report ending in `Overall: PASS` / `NEEDS REVIEW`
  - Falls back to deterministic `report_agent.py` if Gemini is unavailable
- **`app/agents/orchestrator_agent.py`** — Orchestrator Agent (agentic, opt-in via `ORCHESTRATOR_AGENT=true`)
  - True tool-calling agent (8 tools, up to 25 turns) that reasons about each step instead of running the fixed flow
  - Enforces the same invariants explicitly: **won't migrate without a confirmed backup**, escalates all rows to HITL on critic error-findings, flags unusual runs (>75% rows flagged — not an escalation, just a warning)
  - Delegates to the same sub-agents + `pipeline_helpers`, so writes are equivalent to the deterministic path
- **`app/api/agent.py`** — Orchestration entry point
  - `POST /api/agent/run` — synchronous run; `POST /api/agent/run/async` — background run + poll
  - `_execute_migration()` delegates to the Orchestrator Agent when `ORCHESTRATOR_AGENT=true`, else runs the deterministic pipeline
  - Steps: **Backup source** → Run agent → Write staging → Mark source → Store plan → Run critic → Auto-approve clean rows → Cleanup
- **`app/api/pipeline_helpers.py`** — Shared pipeline steps imported by both `agent.py` and `orchestrator_agent.py` (avoids circular imports)
  - `write_to_staging`, `store_migration_plan`, `mark_source_migrating`, `force_all_to_review`, `purge_committed_sources`
- **`app/core/backup.py`** — Pre-migration source snapshot (enterprise infra, not agent logic)
  - Providers via `BACKUP_PROVIDER`: `aws_rds` (boto3), `supabase` (Management API), `webhook` (generic HTTP), `none` (dev)
  - Never reads/copies data rows — only invokes the provider and records the snapshot id; non-blocking in the deterministic path
- **`app/core/backup_store.py`** — Zero-setup SQLite log of backup metadata at `backend/data/backup_log.db`
- **`app/api/migration.py`** — HITL pipeline
  - `_promote_rows()` — Generic promotion using agent's `promotion_config` (no hardcoded schema)
  - `_resolve_conflict_target()` — Validates agent's proposed ON CONFLICT keys against **real** `pg_constraint` catalog (NOT `information_schema` — that view returns empty on Supabase pooler connections); falls back to the actual UNIQUE constraint
  - `auto_approve_clean_rows()` — Auto-promote `risk_level='auto'` rows
  - `approve()` / `reject()` / `rollback()` endpoints with full audit trail
- **`app/api/abase.py`** — Read ABASE data (list scientists, drill into experiments)
- **`app/api/gds.py`** — Read GDS data (list promoted scientists with EC50/R², drill into compound experiments, plate heatmap endpoint)
- **`app/connectors/`** — Async PostgreSQL pool management; shared `_create_pool(url)` decodes percent-encoded passwords

#### Frontends (Next.js + React)
- **`frontend/`** — Main HITL console (localhost:3000) — run migration, review flagged wells, generate reports
- **`abase-frontend/`** — ABASE legacy data viewer (localhost:3001)
- **`gds-frontend/`** — GDS target data viewer (localhost:3002)

### Database Schema
- **`schema_abase.sql`** — ABASE tables: `users`, `experiments`
- **`schema_gds.sql`** — GDS tables:
  - `gds_users` — scientists by name (UUID primary key)
  - `gds_staging_experiments` — UNLOGGED table, fast transient buffer (data loss on crash intentional)
  - `gds_experiments` — production compound experiments, UNIQUE(gds_user_id, compound_id), immutable once approved; columns: ec50_um, hill_slope, r_squared, curve_quality, num_concentration_points, curve_data (JSONB), plate_barcode, neg_ctrl_mean, pos_ctrl_mean
  - `migration_plans` — stores agent's `promotion_config` JSON per trace_id
  - `migration_audit_log` — append-only audit trail
  - `migration_mappings` — schema-fingerprint → cached transform (skips LLM on repeated runs)
  - `migration_jobs` — async job tracking (queued / running / succeeded / failed)
  - `migration_source_backups` — restore-point metadata per run (snapshot id, source host, per-table row counts); read via `GET /api/migrate/restore-point/{trace_id}`
- **`backend/data/backup_log.db`** — local SQLite backup log (auto-created, no setup)

## Demo Data (`seed_abase_v2.sql`)

**20 scientists, 240 wells** across 3 plates (8 scientists per plate, 12 wells per scientist row).

Each scientist occupies one row (A–H) on a plate:
- **Columns 01–10**: sample wells — 10 log-spaced concentrations for dose-response fitting
- **Column 11**: negative control (DMSO, ~88,000–92,000 RFU — 0 % inhibition baseline)
- **Column 12**: positive control (reference inhibitor, ~5,000–8,000 RFU — 100 % inhibition)

The Migration Agent groups the 10 sample wells per (scientist, compound) into one dose-response curve, normalizes raw RFU → % inhibition using the plate controls, fits the Hill equation (scipy curve_fit), and classifies each curve by R²:
- **R² ≥ 0.90** → `risk_level='auto'` (auto-promotes, no human needed)
- **R² < 0.90** → `risk_level='review'` (held in staging for HITL)

**~10 compounds are seeded with poor-quality curves** (intentionally noisy RFU values so that R² falls below 0.90), giving reviewers something to inspect in every demo run. The remaining ~10 compounds produce clean sigmoid curves (R² ≥ 0.95 — `excellent`) and auto-promote.

## Commands

### Demo Reset (run before every demo session)
```bash
cd backend
python scripts/reset_demo.py
# Expected output ends with:
#   ABASE → 20 scientists, 240 wells
#   GDS   → 0 users, 0 experiments, 0 staging rows
#   Status: READY FOR DEMO ✓
```
The script verifies counts after both TRUNCATE and seed; exits with error if anything is wrong.

### Backend
```bash
cd backend
pip install -r requirements.txt
python -m uvicorn app.main:app --reload --port 8001
curl http://localhost:8001/health
```

### Frontends
```bash
cd frontend      && npm install && npm run dev   # localhost:3000
cd abase-frontend && npm install && npm run dev  # localhost:3001
cd gds-frontend   && npm install && npm run dev  # localhost:3002
```

## Key Concepts

### Agent Genericity
The Migration Agent **never knows about ABASE or GDS by name**. It only knows:
- `source_url` / `target_url` — any PostgreSQL connection strings
- `information_schema` — reads actual table/column names at runtime
- `promotion_config` JSON — the agent **outputs** a mapping; infrastructure reads it back

Works for ANY PostgreSQL → PostgreSQL migration. ABASE → GDS is just the default.

### Curve Quality Screening (R²-based)
When the source has a concentration column, the Migration Agent fits a Hill equation to each (scientist, compound) dose-response series:
```
R² ≥ 0.90  →  risk_level = 'auto'    (good fit — auto-promote)
R² < 0.90  →  risk_level = 'review'  (poor fit — hold for HITL)

curve_quality label:
  R² ≥ 0.95  →  'excellent'
  R² ≥ 0.90  →  'good'
  R² ≥ 0.80  →  'fair'
  R² < 0.80  →  'poor'
```
Individual curve points are also classified: `|residual| > 3σ` → `'critical'` (shown as X on the curve plot).

The Verification Agent **independently recomputes** R² thresholds and compares classifications to audit the Migration Agent's work.

### Partial HITL
- **Auto rows** (`risk_level='auto'`): safe → auto-promote to `gds_experiments` immediately
- **Review rows** (`risk_level='review'`): anomalous → held in `gds_staging_experiments` → human approves/rejects via `/review?trace_id=XXX` UI or Review Resolution Agent

### Trace ID
UUID `trace_id` flows through: agent run → staging rows → `migration_plans` → `migration_audit_log` → HITL decisions → verification report. Use it to audit, roll back, or inspect any run.

## Database Connections

**Special character handling:** ABASE password contains `@`. Solution: `.env` percent-encodes it (`%40`); `_create_pool()` calls `urllib.parse.unquote()` before connecting.

**Supabase pooler caveat:** `_resolve_conflict_target()` in `migration.py` queries `pg_constraint` + `pg_class` + `pg_attribute` (NOT `information_schema`) to discover real UNIQUE constraints. `information_schema.key_column_usage` returns empty rows on Supabase's pgBouncer pooler sessions, which caused a silent fallback to unvalidated ON CONFLICT keys and a PostgreSQL `InvalidColumnReferenceError`. Always use `pg_catalog` for DDL introspection on Supabase.

**Connection pooling:** Async pools with min_size=1, max_size=5. Lifespan managed by FastAPI.

## Debugging

### Check Both DBs at Once
```bash
cd backend && python3 << 'EOF'
import asyncio, os, urllib.parse
import asyncpg
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(".env"))

async def check():
    for label, env_var in [("ABASE", "ABASE_DATABASE_URL"), ("GDS", "GDS_DATABASE_URL")]:
        url = os.getenv(env_var)
        p = urllib.parse.urlparse(url)
        conn = await asyncpg.connect(
            host=p.hostname, port=p.port or 5432,
            user=urllib.parse.unquote(p.username or ""),
            password=urllib.parse.unquote(p.password or ""),
            database=p.path.lstrip("/"), ssl="require", statement_cache_size=0
        )
        if label == "ABASE":
            print(f"ABASE → {await conn.fetchval('SELECT COUNT(*) FROM users')} scientists, "
                  f"{await conn.fetchval('SELECT COUNT(*) FROM experiments')} wells")
        else:
            print(f"GDS   → users={await conn.fetchval('SELECT COUNT(*) FROM gds_users')}, "
                  f"experiments={await conn.fetchval('SELECT COUNT(*) FROM gds_experiments')}, "
                  f"staging={await conn.fetchval('SELECT COUNT(*) FROM gds_staging_experiments')}")
        await conn.close()

asyncio.run(check())
EOF
```

### Check Pending Review Rows
```bash
curl "http://localhost:8001/api/migrate/staging/<trace_id>"
```

### Agent Reasoning
Agent logs include every tool call, the pandas script attempts, and self-repair loops.
```bash
tail -f backend/logs/backend.log
```

## Gemini API Key

Key location: `backend/.env` as `GEMINI_API_KEY`. Billing-enabled key strongly recommended.

**Free tier:** 5 requests/minute — a single agent run can exhaust it. If you get 503s:
1. Go to https://aistudio.google.com/app/apikey
2. Create a new key and copy it immediately (navigating away invalidates it)
3. Update `.env` and restart the server

**Model failover chain:** `gemini-2.5-flash` → `gemini-2.0-flash` → `gemini-2.5-flash-lite` (in `app/core/llm.py`). Transient errors retry across the chain; non-transient errors surface immediately.

## Important Notes

- **Agents are read-only by design** — all DB writes happen in `api/agent.py` and `api/migration.py`
- **No hardcoded database identifiers** — table/column names come from `information_schema` at runtime or from the agent's `promotion_config` JSON
- **`promotion_config` is the glue** — agent outputs it; infrastructure reads it back generically
- **UNLOGGED staging table is intentional** — fast, no WAL overhead; data loss on crash is acceptable for transient staging
- **Production records are immutable** — `UNIQUE(gds_user_id, compound_id)` prevents duplicates on re-migration (each row represents one compound's fitted curve, not a single well)
- **ABASE cleanup is post-commit** — scientists are hard-deleted from ABASE only after ALL their compound experiments are confirmed live in GDS; partial migrations leave source intact
- **Source is backed up before any change** — `app/core/backup.py` snapshots the source via `BACKUP_PROVIDER`; non-blocking in the deterministic path, mandatory under the Orchestrator Agent
- **Two orchestration modes** — `ORCHESTRATOR_AGENT=false` (default) runs the fixed pipeline; `ORCHESTRATOR_AGENT=true` runs the agentic `orchestrator_agent.py`. Both share `pipeline_helpers.py`
- **Critic escalation is severity-based** — only a `FLAG` with an `error`-severity finding forces all rows to HITL; warnings/info never block auto-promotion

## Environment Flags

| Env var | Default | Effect |
|---|---|---|
| `ORCHESTRATOR_AGENT` | `false` | `true` drives the migration with the agentic Orchestrator instead of the fixed pipeline |
| `BACKUP_PROVIDER` | `none` | `aws_rds` / `supabase` / `webhook` enable a pre-migration source snapshot (`none` = dev, warns) |
| `BACKUP_POLL_INTERVAL_SEC` / `BACKUP_POLL_TIMEOUT_SEC` | `15` / `600` | Backup status polling cadence and max wait |

Provider-specific backup vars (AWS: `BACKUP_AWS_*`; Supabase: `BACKUP_SUPABASE_*`; webhook: `BACKUP_WEBHOOK_*`) are documented in `app/core/backup.py`.
