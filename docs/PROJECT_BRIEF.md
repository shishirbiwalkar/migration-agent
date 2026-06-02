# Project Brief — Migration Agent Platform

An AI-driven enterprise data migration platform. Moves records from a legacy system (ABASE) to a new system (GDS) with full schema discovery, anomaly screening, human-in-the-loop review, and an auditable verification report — all without any agent ever writing to a database.

---

## The Problem

Enterprise data migrations fail in two ways: silent data corruption (wrong mapping nobody noticed) and "human bottleneck" (every row needs manual sign-off). This platform solves both:
- The AI maps schemas and screens anomalies automatically
- Only the genuinely uncertain rows go to a human
- An independent agent audits the result

---

## System Overview

```
ABASE (legacy, Supabase us-west-2)    GDS (target, Supabase us-east-2)
  users + experiments            →      gds_users + gds_experiments
                                         (via staging → production)

FastAPI backend (:8001)
  Migration Agent      reads source, proposes mapping, screens anomalies
  Mapping Critic       audits the mapping before any data moves
  Review Resolution    translates researcher messages → approve/exclude
  Verification Agent   independently audits the completed migration

Three Next.js frontends
  :3000  HITL Console   run migration, review flagged wells, read reports
  :3001  ABASE viewer   browse source data
  :3002  GDS viewer     browse migrated data
```

---

## Reference Dataset

**20 scientists, 80 wells** (4 wells per scientist).

**10 wells intentionally flagged** for HITL demo:
| Scientist | Flagged wells | Scenario |
|---|---|---|
| Chen_L | B03 (63.00), B04 (62.00) | Batch contamination — 2 spikes on same plate |
| Singh_A | B03 (68.40), B04 (66.00) | Repeated anomaly — same scientist, 2 wells |
| Williams_K | B04 (65.90) | Single saturation spike |
| Mueller_T | A02 (63.00) | Contamination |
| Gupta_P | B03 (62.00) | Above-threshold reading |
| Lee_H | B03 (71.30) | Saturated detector |
| Brown_E | A04 (62.00) | Pipetting error |
| Walsh_D | B04 (74.80) | Clear anomaly |

70 normal wells (signal 5–15) auto-promote without human review.

---

## Core Design Rules

1. **Agents never write to a database.** The only writers are `api/agent.py` and `api/migration.py`.
2. **The LLM never computes exact figures.** All math (thresholds, counts, reconciliation) runs in pandas/numpy or SQL.
3. **`promotion_config` is the contract.** The Migration Agent outputs a JSON mapping; all downstream code (promotion, review, verification) consumes it without re-asking the LLM.
4. **Every write is attributed.** Actor + timestamp in `migration_audit_log` for every auto-approve, approve, exclude, reject.
5. **Source is never deleted before target is confirmed.** ABASE scientists are hard-deleted only after all their wells are live in GDS.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.11+, FastAPI, asyncpg |
| AI | Google Gemini (`google-genai` SDK), model failover chain |
| Data transform | pandas, numpy |
| Frontends | Next.js 14 / React / Tailwind CSS (×3) |
| Database | PostgreSQL via Supabase (managed, no infra to run) |

---

## Key Endpoints

| Method | Path | What it does |
|---|---|---|
| `POST` | `/api/agent/run` | Full migration: discover → transform → screen → stage → auto-promote |
| `GET` | `/api/migrate/staging/{trace_id}` | Pending review rows for a run |
| `POST` | `/api/migrate/approve/{trace_id}` | HITL approve — promotes flagged rows to production |
| `POST` | `/api/migrate/reject/{trace_id}` | HITL reject |
| `POST` | `/api/migrate/rollback/{trace_id}` | Roll back an approved run |
| `POST` | `/api/report/{trace_id}` | Generate verification report |
| `GET` | `/api/abase/users` | List source scientists |
| `GET` | `/api/gds/users` | List promoted scientists |

---

## Demo Flow

```
python backend/scripts/reset_demo.py    # → READY FOR DEMO ✓

POST /api/agent/run
  → 70 wells auto-promoted, 10 flagged (Chen_L ×2, Singh_A ×2, 6 others ×1)

Open localhost:3000/review?trace_id=<id>
  → Approve / exclude / reject flagged wells

POST /api/report/<trace_id>
  → Verification Agent produces enterprise report with Overall: PASS
```

---

## What Is and Isn't Done

**Done:**
- Full 4-agent system (Migration, Critic, Review Resolution, Verification)
- HITL console with approve/exclude/reject/rollback
- Mapping cache (skips LLM on repeated schema runs)
- Async run path (`POST /api/agent/run/async` + job polling)
- Full audit trail with attribution
- Model failover (`gemini-2.5-flash → 2.0-flash → 2.5-flash-lite`)
- Three themed frontends (ABASE light, GDS dark, HITL console)

**Not done (future):**
- Docker Compose / containerized deployment
- Automated test suite
- Durable async queue (current async is in-process, doesn't survive restart)
- Orchestrator agent (current pipeline is a fixed linear sequence)
- Notification system (HITL review is pull-based, no email/Slack)
