# Migration Agent

An **agentic, AI-driven data migration platform**. It discovers the schemas of a source and
target PostgreSQL database at runtime, reasons about how to map columns between them, transforms
and quality-screens the data, routes anomalies to a human for review, and produces an
executive-grade verification report — all with a full, attributable audit trail.

> **Core principle:** the AI agents *reason and decide*; deterministic infrastructure code does
> all computation and **every database write**. No agent ever writes to a database. This is what
> makes the system both intelligent and fully auditable.

The reference deployment migrates **ABASE** (a legacy lab system) → **GDS** (the new drug-screening
platform). ABASE stores raw fluorescence (RFU) readings per well. GDS stores fitted dose–response
parameters (EC50, Hill slope, R², Emax) plus normalized % inhibition curves — a semantically
richer, analysis-ready schema. The agents figure out this mapping at runtime; ABASE → GDS is simply
the default when no URLs are supplied.

---

## What's in the box

| Component | Type | Role |
|---|---|---|
| **Migration Agent** | Agentic (tool loop, 5 tools, self-repair) | Discovers schemas, maps columns, writes a pandas+scipy transform, fits Hill equations, normalizes RFU → % inhibition, screens curves by R² |
| **Orchestrator Agent** *(opt-in)* | Agentic (tool loop, 8 tools, ≤25 turns) | Reasons over the whole pipeline and delegates to sub-agents; refuses to proceed without a confirmed backup. Enable with `ORCHESTRATOR_AGENT=true` |
| **Mapping Critic** | Single LLM call | Audits the proposed column mapping before any data moves; severity-based escalation to HITL |
| **Review Resolution Agent** | Agentic (tool loop, 5 tools) | Turns a researcher's plain-English message into approve/exclude decisions |
| **Verification Agent** | Agentic (tool loop, 8 read-only tools) | Independently audits a completed migration and writes the verification report |
| Source backup | Deterministic infra | Pre-migration snapshot of the source DB (pg_dump / AWS RDS / Supabase / webhook) |
| Orchestration & writes | Deterministic code | All staging/promotion/audit writes, upsert-key guard, HITL API |

### Documentation

| Doc | Covers |
|---|---|
| **[ARCHITECTURE.md](ARCHITECTURE.md)** | Full system design and component taxonomy |
| **[WORKFLOW.md](WORKFLOW.md)** | End-to-end pipeline and HITL gates |
| **[docs/FAILURE-MODES.md](docs/FAILURE-MODES.md)** | LLM unavailability and hallucination handling |
| **[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)** | How to deploy and fallback behaviour |
| **[docs/SCALING.md](docs/SCALING.md)** | The five scaling ceilings and how to raise them |
| **[CONTRIBUTING.md](CONTRIBUTING.md)** | Developer guide |

---

## Architecture at a glance

```
   ┌──────────────────┐     ┌──────────────── Backend (FastAPI, :8001) ──────────────────────┐
   │  SOURCE DB       │     │                                                                 │
   │  ABASE           │─────▶  Migration Agent ──▶ Mapping Critic ──▶ deterministic promotion │
   │  raw RFU wells   │     │   (read-only)          (audit)            (the ONLY writer)     │
   └──────────────────┘     │        │                                        │               │
                            │        ▼                                        ▼               │
   ┌──────────────────┐     │  promotion_config (JSON glue)          staging → production      │
   │  TARGET DB       │◀────┤                                                │               │
   │  GDS             │     │  Review Resolution Agent ─────▶ resolves flagged rows (HITL)    │
   │  EC50/Hill/R²    │     │  Verification Agent ──────────▶ enterprise verification report  │
   └──────────────────┘     └─────────────────────────────────────────────────────────────────┘
        ▲                                  ▲                        ▲
   ABASE viewer (:3001)          HITL Console (:3000)        GDS viewer (:3002)
   raw wells + RFU           run · review · report         EC50 · plate heatmap
                                                           normalization window
```

---

## Quickstart

### Prerequisites
- Python 3.11+
- Node.js 18+
- Two PostgreSQL databases (the reference setup uses Supabase)
- A Google Gemini API key (billing-enabled recommended — see [Notes](#notes))

### 1. Backend

```bash
cd backend
cp .env.example .env          # fill in real values
pip install -r requirements.txt
python -m uvicorn app.main:app --reload --port 8001
# health check:
curl http://localhost:8001/health
```

Logs are written to `backend/logs/backend.log` (and the console).

### 2. Frontends

Three independent Next.js apps. The API base URL is `http://localhost:8001` (see each app's
`app/page.tsx`).

```bash
# HITL Console — run migration, review flagged curves, view reports
cd frontend && npm install && npm run dev          # http://localhost:3000

# ABASE viewer — browse the legacy source data
cd abase-frontend && npm install && npm run dev    # http://localhost:3001

# GDS viewer — browse the migrated target data with dose–response curves
cd gds-frontend && npm install && npm run dev      # http://localhost:3002
```

### 3. Reset to a clean demo state

```bash
cd backend
python scripts/reset_demo.py
# Expected output:
#   ABASE → 20 scientists, 240 wells
#   GDS   → 0 users, 0 experiments, 0 staging rows
#   Status: READY FOR DEMO ✓
```

---

## Using it

1. Open the **HITL Console** at `http://localhost:3000`.
2. **Run migration** — the agent discovers schemas, fits dose–response curves, normalizes RFU
   to % inhibition using control wells, classifies curves by R², auto-promotes clean ones, and
   stages poor-fit curves for review.
3. **Review flagged curves** — each flagged record shows EC50, Hill slope, R², and a full
   dose–response SVG. Approve, exclude, or reject. Every decision is recorded with owner and
   timestamp.
4. **Generate the verification report** — the Verification Agent audits the run and produces a
   business report ending in `Overall: PASS` / `NEEDS REVIEW`.
5. **Browse GDS** at `http://localhost:3002` — scientists, per-compound EC50 metrics, 96-well
   plate heatmap (cols 11–12 show DMSO and reference inhibitor control wells), dose–response
   curve with normalization window (DMSO baseline · Ref inhibitor · assay window in RFU).

Every run is tagged with a `trace_id` that flows through staging, the audit log, and the report —
use it to inspect, audit, or roll back a specific migration.

---

## Project structure

```
.
├── backend/                  FastAPI service — agents, APIs, connectors
│   ├── app/
│   │   ├── agents/           Migration, Orchestrator, Review, Verification, Critic, (report fallback)
│   │   ├── api/              HTTP endpoints — all database writes live here, never in agents
│   │   ├── connectors/       Async PostgreSQL pool management
│   │   └── core/             Gemini client + failover (llm.py), mapping validation (mapping.py)
│   ├── data/                 backup_log.db (SQLite), backups/ (pg_dump files)
│   ├── scripts/reset_demo.py Demo reset / re-seed
│   ├── schema_abase.sql      Source schema (users, experiments)
│   ├── schema_gds.sql        Target schema (gds_users, gds_experiments, staging, audit log …)
│   └── seed_abase_v2.sql     Reference source data (20 scientists, 240 wells, 10 plates)
├── frontend/                 HITL console (:3000)
├── abase-frontend/           Source viewer (:3001)
├── gds-frontend/             Target viewer (:3002) — dose–response + 96-well plate heatmap
├── docs/                     Operations: failure modes, deployment, scaling
├── ARCHITECTURE.md           System design
├── WORKFLOW.md               End-to-end pipeline & HITL gates
└── CONTRIBUTING.md           Developer guide
```

---

## Notes

- **Gemini reliability:** the call wrapper (`app/core/llm.py`) fails over across Gemini models
  (`gemini-2.5-flash` → `gemini-2.0-flash` → `gemini-2.5-flash-lite`) on transient overload,
  then backs off. A billing-enabled key is strongly recommended for demos — the free tier returns
  503s under load.
- **Read-only agents:** agents never execute writes. All writes happen in `app/api/migration.py`
  and `app/api/agent.py`, behind validation and a unique-constraint guard.
- **Security:** the real `.env` is git-ignored. Never commit credentials. All identifiers from
  the agent's `promotion_config` are validated before use in SQL.

---

## License

See [LICENSE](LICENSE). Proprietary — internal use.
