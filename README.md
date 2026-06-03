# Migration Agent

An **agentic, AI-driven data migration platform**. It discovers the schemas of a source and
target PostgreSQL database at runtime, reasons about how to map columns between them, transforms
and quality-screens the data, routes anomalies to a human for review, and produces an
executive-grade verification report — all with a full, attributable audit trail.

> **Core principle:** the AI agents *reason and decide*; deterministic infrastructure code does
> all computation and **every database write**. No agent ever writes to a database. This is what
> makes the system both intelligent and fully auditable.

The reference deployment migrates **ABASE** (a legacy lab system) → **GDS** (the new system), but
the agents are schema-agnostic: point them at any two PostgreSQL databases and they work out the
mapping. ABASE → GDS is simply the default when no URLs are supplied.

---

## What's in the box

| Component | Type | Role |
|---|---|---|
| **Migration Agent** | 🟢 Agentic (tool loop, 5 tools, self-repair) | Discovers schemas, maps columns, transforms data, screens for anomalies |
| **Review Resolution Agent** | 🟢 Agentic (tool loop, 5 tools) | Turns a researcher's plain-English message into approve/exclude decisions |
| **Verification Agent** | 🟢 Agentic (tool loop, 8 read-only tools) | Independently audits a completed migration and writes the verification report |
| **Orchestrator Agent** *(opt-in)* | 🟢 Agentic (tool loop, 8 tools) | Reasons over the whole pipeline and delegates to the sub-agents; enable with `ORCHESTRATOR_AGENT=true` |
| **Mapping Critic** | 🟡 Single LLM call | Reviews the proposed mapping before any data moves (proposer–critic); severity-based escalation |
| Source backup | ⚪ Deterministic infra | Pre-migration snapshot of the source DB (AWS RDS / Supabase / webhook) before any change |
| Orchestration & writes | ⚪ Deterministic code | All staging/promotion/audit writes, the upsert-key guard, the API |

### Documentation

| Doc | Covers |
|---|---|
| **[ARCHITECTURE.md](ARCHITECTURE.md)** | Full system design |
| **[WORKFLOW.md](WORKFLOW.md)** | End-to-end pipeline & HITL gates |
| **[docs/FAILURE-MODES.md](docs/FAILURE-MODES.md)** | LLM unavailability & hallucination handling |
| **[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)** | How to deploy, and fallback behaviour |
| **[docs/SCALING.md](docs/SCALING.md)** | The five scaling ceilings and how to raise them |
| **[CONTRIBUTING.md](CONTRIBUTING.md)** | Developer guide |

---

## Architecture at a glance

```
   ┌─────────────┐        ┌──────────────────── Backend (FastAPI, :8001) ────────────────────┐
   │  SOURCE DB  │        │                                                                   │
   │  (ABASE)    │──read──▶  Migration Agent ──▶ Mapping Critic ──▶ deterministic promotion   │
   └─────────────┘        │   (read-only)          (audit)            (the ONLY writer)       │
                          │        │                                        │                 │
   ┌─────────────┐        │        ▼                                        ▼                 │
   │  TARGET DB  │◀─write─┤   promotion_config (JSON glue)          staging → production       │
   │  (GDS)      │        │                                          │                         │
   └─────────────┘        │   Review Resolution Agent ──▶ resolves flagged rows (HITL)         │
                          │   Verification Agent ──────▶ enterprise verification report        │
                          └───────────────────────────────────────────────────────────────────┘
        ▲                                  ▲                    ▲
   ABASE viewer (:3001)            HITL Console (:3000)    GDS viewer (:3002)
```

---

## Quickstart

### Prerequisites
- Python 3.11+
- Node.js 18+
- Two PostgreSQL databases (the reference setup uses Supabase)
- A Google Gemini API key (billing enabled recommended — see [Notes](#notes))

### 1. Backend

```bash
cd backend
cp .env.example .env          # then fill in real values
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
# HITL Console — run migration, review flagged rows, view reports
cd frontend && npm install && npm run dev          # http://localhost:3000

# ABASE viewer — browse the legacy source data
cd abase-frontend && npm install && npm run dev    # http://localhost:3001

# GDS viewer — browse the migrated target data
cd gds-frontend && npm install && npm run dev      # http://localhost:3002
```

### 3. Reset to a clean demo state

```bash
cd backend
python scripts/reset_demo.py     # clears GDS, re-seeds ABASE (20 researchers / 80 records)
```

---

## Using it

1. Open the **HITL Console** at `http://localhost:3000`.
2. **Run migration** — the agent migrates the source → target, auto-promotes clean records, and
   stages anomalous ones for review.
3. **Review** the flagged records — approve, exclude, or reject. Every decision is recorded with
   its owner and timestamp.
4. **Generate the verification report** — the Verification Agent audits the run and produces a
   business report ending in an `Overall: PASS` / `NEEDS REVIEW` sign-off.

Every run is tagged with a `trace_id` that flows through staging, the audit log, and the report —
use it to inspect, audit, or roll back a specific migration.

---

## Project structure

```
.
├── backend/                  FastAPI service — agents, APIs, connectors
│   ├── app/
│   │   ├── agents/           Migration, Review, Verification, Critic, (report fallback)
│   │   ├── api/              HTTP endpoints (agent, migration, abase, gds, report, …)
│   │   ├── connectors/       Async PostgreSQL pool management
│   │   ├── core/             Shared: Gemini client + failover, mapping helpers
│   │   └── main.py           App entrypoint, dual-pool lifespan, logging
│   ├── scripts/reset_demo.py Demo reset / re-seed
│   ├── schema_*.sql          Source & target schemas
│   └── seed_abase_v2.sql     Reference source data
├── frontend/                 HITL console (:3000)
├── abase-frontend/           Source viewer (:3001)
├── gds-frontend/             Target viewer (:3002)
├── docs/                     Operations: failure modes, deployment, scaling
├── ARCHITECTURE.md           System design
├── WORKFLOW.md               End-to-end pipeline & HITL gates
└── CONTRIBUTING.md           Developer guide
```

---

## Notes

- **Gemini reliability:** the call wrapper (`app/core/llm.py`) fails over across Gemini models
  (`gemini-2.5-flash` → `gemini-2.0-flash` → `gemini-2.5-flash-lite`) on transient overload (503),
  then backs off. A billing-enabled key is strongly recommended for demos — the free tier returns
  503s under load.
- **Read-only agents:** agents never execute writes. All writes happen in `app/api/migration.py`
  and `app/api/agent.py`, behind validation and a unique-constraint guard.
- **Security:** the real `.env` is git-ignored. Never commit credentials. All identifiers coming
  from the agent's `promotion_config` are validated before use in SQL.

---

## License

See [LICENSE](LICENSE). Proprietary — internal use.
