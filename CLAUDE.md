# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Migration Agent** is an AI-driven, read-only data migration system that discovers source/target database schemas at runtime, reasons about column mapping, generates pandas transformation scripts, classifies rows by anomaly detection, and stages cleaned data for human-in-the-loop review.

**Core principle:** Agent is purely computational and never writes to any database. All DB writes are handled by deterministic infrastructure code, leaving a full audit trail.

## Architecture

### Dual-Database Setup
- **ABASE** (Supabase us-west-2): Source system with `users` and `experiments` tables
- **GDS** (Supabase us-east-2): Target system with `gds_users`, `gds_staging_experiments`, `gds_experiments` tables
- Both URLs are stored in `.env` with percent-encoded special characters (password contains `@` encoded as `%40`)

### Key Components

#### Backend (Python + FastAPI)
- **`app/main.py`** — FastAPI app, dual pool lifespan, CORS, trace ID middleware
- **`app/agents/migration_agent.py`** — Core agent (300+ lines) — truly generic, read-only
  - Accepts `source_url` and `target_url` parameters (works with ANY PostgreSQL databases)
  - Discovers schemas via `information_schema` at runtime
  - Samples real rows, transforms in-memory with pandas + numpy
  - Classifies rows using statistical anomaly detection (mean ± 2σ)
  - Returns `cleaned_records` + `promotion_config` JSON (schema mapping)
  - Tool self-repair loop: on script error, agent reads traceback and rewrites
  - Per-run state via `_RunState` dataclass (no module-level globals)
- **`app/api/agent.py`** — Agent orchestration
  - `POST /api/agent/run` accepts optional `source_db_url` and `target_db_url`
  - Step 1: Run agent (read-only), Step 2: Write to staging, Step 3: Store promotion config, Step 4: Auto-approve 'auto' rows
  - Returns counts of auto-approved vs. pending-review rows
- **`app/api/migration.py`** — HITL pipeline
  - `_promote_rows()` — Generic function using agent's `promotion_config` JSON to promote ANY schema (not hardcoded)
  - `auto_approve_clean_rows()` — Auto-promote rows with risk_level='auto'
  - `approve()` endpoint — Human approves review rows
  - `reject()` endpoint — Human rejects review rows
  - Full audit trail via `migration_audit_log` table
- **`app/api/abase.py`** — Read ABASE data
  - `GET /api/abase/users` — List ABASE scientists
  - `GET /api/abase/users/{id}` — Scientist detail + experiments
  - `GET /api/abase/experiments` — All experiments joined with scientist names (fed to agent)
- **`app/api/gds.py`** — Read GDS data
  - `GET /api/gds/users` — List GDS scientists with avg_signal summary
  - `GET /api/gds/users/{id}/experiments` — Drill-down into individual wells
  - `GET /api/gds/experiments` — All production well records
- **`app/connectors/`** — Async PostgreSQL pool management
  - Shared `_create_pool(url)` function handles URL parsing and special character decoding

#### Frontends (Next.js + React)
- **`frontend/`** — Main HITL review UI (localhost:3000) — shows pending rows, approve/reject/rollback
- **`abase-frontend/`** — ABASE legacy data viewer (localhost:3001)
- **`gds-frontend/`** — GDS target data viewer (localhost:3002)

### Database Schema
- **`schema_abase.sql`** — ABASE tables: `users` (8 scientists), `experiments` (25 wells)
- **`schema_gds.sql`** — GDS tables:
  - `gds_users` — scientists by name (UUID primary key)
  - `gds_staging_experiments` — UNLOGGED table, fast transient buffer (data lost on crash intentional)
  - `gds_experiments` — production wells, immutable once approved
  - `migration_plans` — stores agent's `promotion_config` JSON per trace_id
  - `migration_audit_log` — full audit trail

## Commands

### Backend

```bash
cd backend

# Install dependencies
pip install -r requirements.txt

# Run development server (auto-reload, port 8001)
python -m uvicorn app.main:app --reload --port 8001

# Health check
curl http://localhost:8001/health
```

### Frontends

```bash
# Each frontend is independent Next.js app

# Main HITL UI
cd frontend
npm install
npm run dev    # localhost:3000

# ABASE viewer
cd abase-frontend
npm install
npm run dev    # localhost:3001

# GDS viewer
cd gds-frontend
npm install
npm run dev    # localhost:3002
```

## Key Concepts

### Agent Genericity
The agent **never knows about ABASE or GDS by name**. It only knows:
- `source_url` — any PostgreSQL connection string
- `target_url` — any PostgreSQL connection string
- `information_schema` — reads actual table/column names at runtime
- `promotion_config` JSON — the agent **outputs** a mapping; infrastructure reads it back

This means the agent works for ANY PostgreSQL → PostgreSQL migration. ABASE → GDS is just the default when no URLs are passed.

### Anomaly Detection
The agent computes thresholds dynamically from actual data:
```
mean_value = avg of all signal values in sample
std_dev = standard deviation
threshold = mean ± 2σ
```
Rows outside this range are marked `risk_level='review'`; inside are `risk_level='auto'`.

### Partial HITL
- **Auto rows**: Agent classified them as safe → auto-promote to `gds_experiments` immediately (no human needed)
- **Review rows**: Anomalous → stage in `gds_staging_experiments` → human approves/rejects via `/review?trace_id=XXX` UI

### Trace ID
Every run is tagged with a UUID `trace_id` that flows through:
1. Agent run request
2. Staging table (all rows)
3. Audit log (one entry per trace)
4. HITL approve/reject decisions

Use `trace_id` to audit, rollback, or inspect a specific migration run.

## Database Connections

**Special character handling:** ABASE password contains `@` symbol. Solution:
1. `.env` stores it percent-encoded: `Supabase%402799`
2. `_create_pool()` calls `urllib.parse.unquote()` to decode

**Connection pooling:** Async pools with min_size=1, max_size=5. Lifespan managed by FastAPI.

## Debugging

### Check Staging Data
```bash
# Via API
curl http://localhost:8001/api/gds/experiments

# Show pending review rows
curl "http://localhost:8001/api/migration/review?trace_id=<UUID>"
```

### Check Agent Reasoning
Agent logs include tool calls, pandas script attempts, and repair loops. Check stderr for detail.

### Database State
```python
# Quick status check
python3 << 'EOF'
import asyncio
from app.connectors import get_gds_pool

async def check():
    pool = await get_gds_pool()
    async with pool.acquire() as conn:
        staging = await conn.fetchval("SELECT COUNT(*) FROM gds_staging_experiments")
        prod = await conn.fetchval("SELECT COUNT(*) FROM gds_experiments")
        audit = await conn.fetchval("SELECT COUNT(*) FROM migration_audit_log")
        print(f"Staging: {staging}, Production: {prod}, Audit: {audit}")

asyncio.run(check())
EOF
```

## Gemini API Key

The agent requires a valid Gemini API key with available quota. Key location: `.env` as `GEMINI_API_KEY`.

**Quota issues:** Free tier is 5 requests/minute. If exhausted:
1. Go to https://aistudio.google.com/app/apikey
2. Create a new key
3. **Copy immediately** without navigating away (navigating away invalidates it)
4. Update `.env` and restart server

## Important Notes

- **Agent is read-only by design** — all database writes happen in `api/agent.py` and `api/migration.py`, never in the agent itself
- **No hardcoded database identifiers** — table names, column names, FK relationships all come from `information_schema` or the agent's `promotion_config` JSON
- **Promotion config is the glue** — agent outputs it; infrastructure reads it back generically
- **UNLOGGED staging table is intentional** — fast, no WAL, data lost on crash (acceptable for transient staging)
- **Production wells are immutable** — `gds_experiments` has UNIQUE(gds_user_id, well_position) constraint to prevent duplicates on re-migration
