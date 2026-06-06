# Technical Process Document
## Migration Agent Platform — Enterprise Grade
**Version:** 1.0 | **Classification:** Internal / Technical | **Date:** 2026-06-06

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [System Architecture](#2-system-architecture)
3. [Component Inventory](#3-component-inventory)
4. [End-to-End Migration Process](#4-end-to-end-migration-process)
5. [Agent Workflows](#5-agent-workflows)
6. [Data Flow](#6-data-flow)
7. [The Promotion Config Contract](#7-the-promotion-config-contract)
8. [Human-in-the-Loop Process](#8-human-in-the-loop-process)
9. [Hallucination Defense Stack](#9-hallucination-defense-stack)
10. [Failure Modes & Recovery](#10-failure-modes--recovery)
11. [Database Schema](#11-database-schema)
12. [API Reference](#12-api-reference)
13. [Security & Compliance](#13-security--compliance)
14. [Operational Procedures](#14-operational-procedures)
15. [Deployment Topology](#15-deployment-topology)
16. [Known Limitations & Roadmap](#16-known-limitations--roadmap)

---

## 1. Executive Summary

The Migration Agent Platform is an AI-native enterprise data migration system. It autonomously discovers source and target database schemas at runtime, reasons about semantic column equivalence, transforms data in memory, screens for anomalies statistically, and stages results for human-in-the-loop (HITL) review — all without any AI agent ever writing to a database.

**Core design contract:**
- AI agents are purely computational and read-only
- All database writes are performed by deterministic infrastructure code
- Every write is attributed with actor, timestamp, and trace ID
- Source data is never deleted before target data is confirmed live

**Reference deployment:** ABASE (legacy lab system, Supabase us-west-2) → GDS (drug-screening platform, Supabase us-east-2)

**Genericity:** The platform accepts any `source_url` + `target_url`. ABASE → GDS is the reference migration. The same codebase handles HR, financial, clinical, and any other PostgreSQL-to-PostgreSQL migration without modification.

---

## 2. System Architecture

### 2.1 High-Level Topology

```
┌─────────────────────────────────────────────────────────────────────┐
│                         CLIENT LAYER                                │
│                                                                     │
│  HITL Console       ABASE Viewer      GDS Viewer                   │
│  (localhost:3000)   (localhost:3001)  (localhost:3002)              │
│  Next.js / React    Next.js / React   Next.js / React               │
└──────────────────────────┬──────────────────────────────────────────┘
                           │ HTTP / REST
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│                        BACKEND LAYER                                │
│                  FastAPI (Python 3.11+) — Port 8001                 │
│                                                                     │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐                │
│  │  API Layer  │  │ Agent Layer │  │  Core Layer │                 │
│  │  agent.py   │  │ migration   │  │  llm.py     │                 │
│  │ migration.py│  │ critic      │  │  backup.py  │                 │
│  │  abase.py   │  │ review      │  │  mapping.py │                 │
│  │   gds.py    │  │ verification│  │  notify.py  │                 │
│  │  report.py  │  │ orchestrator│  │             │                 │
│  └─────────────┘  └─────────────┘  └─────────────┘                │
└───────┬───────────────────────────────────────────┬─────────────────┘
        │ asyncpg                                   │ google-genai SDK
        ▼                                           ▼
┌───────────────────────┐                 ┌──────────────────────────┐
│   DATABASE LAYER      │                 │       LLM LAYER          │
│                       │                 │                          │
│  ABASE (Supabase)     │                 │  Google Gemini           │
│  us-west-2 (source)   │                 │  gemini-2.5-flash        │
│                       │                 │  gemini-2.0-flash        │
│  GDS (Supabase)       │                 │  gemini-2.5-flash-lite   │
│  us-east-2 (target)   │                 │  (failover chain)        │
└───────────────────────┘                 └──────────────────────────┘
```

### 2.2 Design Principles

| Principle | Implementation |
|---|---|
| Agents are read-only | No agent has a write tool. All writes are in `api/agent.py` and `api/migration.py` |
| LLM proposes, code verifies | Every LLM claim is validated by deterministic code before execution |
| Promotion config is the contract | Agent emits JSON mapping; all downstream code consumes it generically |
| No hardcoded identifiers | Table/column names discovered at runtime from `information_schema` or read from `promotion_config` |
| Full traceability | UUID `trace_id` flows through every table, every log entry, every audit event |
| Source integrity | Source never deleted until target confirmed live |

### 2.3 Trust Boundary

```
┌──────────────────────────────────────────┐
│  AI ZONE (read-only, proposes)           │
│  migration_agent, critic, verification   │
│  review_agent, orchestrator_agent        │
└─────────────────┬────────────────────────┘
                  │ promotion_config JSON
                  │ cleaned_records list
                  ▼
┌──────────────────────────────────────────┐
│  INFRASTRUCTURE ZONE (writes, executes)  │
│  api/agent.py, api/migration.py          │
│  api/pipeline_helpers.py                 │
└──────────────────────────────────────────┘
```

The AI zone never crosses the trust boundary. The infrastructure zone never asks the AI to make decisions during execution.

---

## 3. Component Inventory

### 3.1 AI Agents

#### Migration Agent — `app/agents/migration_agent.py`
- **Type:** Agentic (tool-calling loop, up to 15 turns)
- **Tools:** 5 (all read-only or in-memory)
- **Responsibility:** Discovers both schemas, samples real data, writes and self-repairs a pandas+scipy transform script, classifies anomalies statistically, emits `promotion_config`
- **Key constraint:** Read-only. No DB writes. No module-level globals (per-run state via `_RunState` dataclass).

#### Orchestrator Agent — `app/agents/orchestrator_agent.py`
- **Type:** Agentic (tool-calling loop, up to 25 turns)
- **Tools:** 8
- **Responsibility:** LLM-driven pipeline driver. Reasons about each step instead of following a fixed sequence.
- **Hard rules enforced:** Will not migrate without a confirmed backup; escalates all rows to HITL on critic error-severity findings; flags runs with >75% rows flagged as unusual (does not escalate — still auto-promotes clean rows)
- **Activation:** `ORCHESTRATOR_AGENT=true` in environment

#### Critic Agent — `app/agents/critic_agent.py`
- **Type:** Single-shot LLM call (not agentic)
- **Responsibility:** Reviews `promotion_config` before any data moves. Returns `APPROVE` or `FLAG` with per-finding severity (`error` / `warning` / `info`)
- **Escalation rule:** Only `FLAG` + at least one `error`-severity finding forces all rows to mandatory HITL. Warnings and info never block auto-promotion.
- **Fail mode:** Fail-open by default (critic unavailability ≠ data risk)

#### Review Resolution Agent — `app/agents/review_agent.py`
- **Type:** Agentic (tool-calling loop)
- **Tools:** 5 (`get_pending_wells`, `approve_well`, `exclude_well`, `approve_all_wells`, `exclude_all_wells`)
- **Responsibility:** Translates researcher plain-English messages into approve/exclude decisions. Writes attributed audit events.

#### Verification Agent — `app/agents/verification_agent.py`
- **Type:** Agentic (tool-calling loop)
- **Tools:** 8 (all read-only SELECT tools)
- **Responsibility:** Mandatory audit battery — row reconciliation, independent R² threshold re-check, staging vs production comparison, per-scientist breakdown
- **Key constraint:** LLM never computes numbers. All figures come from deterministic SQL tools.
- **Fallback:** Deterministic `report_agent.py` if Gemini unavailable

### 3.2 Deterministic Infrastructure

| File | Responsibility |
|---|---|
| `app/api/agent.py` | Pipeline entry point — fixed or orchestrator mode |
| `app/api/pipeline_helpers.py` | Shared steps: write_to_staging, store_migration_plan, mark_source_migrating, force_all_to_review, purge_committed_sources |
| `app/api/migration.py` | HITL pipeline — promote_rows, approve, reject, rollback, auto_approve_clean_rows |
| `app/core/backup.py` | Pre-migration source snapshot via provider |
| `app/core/backup_store.py` | SQLite log of backup metadata |
| `app/core/llm.py` | Gemini client with model failover and exponential backoff |
| `app/core/mapping.py` | Identifier validation, type coercion |

### 3.3 Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.11+, FastAPI, asyncpg |
| AI | Google Gemini via `google-genai` SDK |
| Data transform | pandas, numpy, scipy (`curve_fit`) |
| Frontends | Next.js 14 / React / Tailwind CSS |
| Databases | PostgreSQL via Supabase (managed) |
| Local backup log | SQLite (`backend/data/backup_log.db`) |

---

## 4. End-to-End Migration Process

### 4.1 Process Flow

```
TRIGGER
  POST /api/agent/run
  → assign trace_id (UUID)
        │
        ▼
STEP 1 — SOURCE BACKUP
  trigger_backup() → snapshot source DB
  providers: pg_dump / aws_rds / supabase / webhook / none
  record: snapshot_id → migration_source_backups + backup_log.db
  [Orchestrator: mandatory — will not proceed without confirmed=true]
  [Deterministic pipeline: non-blocking — warns and continues]
        │
        ▼
STEP 2 — MIGRATION AGENT (read-only)
  Tool 1: discover_source_schema    → tables, columns, types, FKs
  Tool 2: discover_target_schema    → staging table, production tables
  Tool 3: sample_source_data        → real rows, real values
  Tool 4: write_and_test_mapping    → AI writes pandas+scipy script
                                      exec() in-memory, self-repair on error
                                      up to 5 retries per tool failure
  Tool 5: store_promotion_config    → JSON mapping stored in agent state
  OUTPUT: cleaned_records + promotion_config
  [ZERO DB WRITES IN THIS STEP]
        │
        ▼
STEP 3 — WRITE TO STAGING
  _write_to_staging(trace_id, cleaned_records, promotion_config)
  → all rows → gds_staging_experiments (status='pending')
  → each row stored as JSONB data blob
        │
        ▼
STEP 4 — MARK SOURCE + STORE PLAN
  mark_source_migrating()  → ABASE users.migration_status = 'migrating'
  store_migration_plan()   → promotion_config → migration_plans (keyed by trace_id)
  [source is marked, NOT deleted]
        │
        ▼
STEP 5 — MAPPING CRITIC (single-shot LLM)
  critic_agent reviews promotion_config
  → verdict: APPROVE or FLAG
  → per-finding severity: error / warning / info
        │
        ├── FLAG + error-severity ──▶ force_all_to_review()
        │                             all rows → risk_level='review'
        │                             auto-promote skipped
        │
        └── APPROVE (or warn/info only)
                │
                ▼
STEP 6 — AUTO-PROMOTE CLEAN ROWS
  auto_approve_clean_rows(trace_id, promotion_config)
  → rows where risk_level='auto' → gds_experiments (production)
  → ON CONFLICT (gds_user_id, compound_id) DO UPDATE
  → audit log: hitl_auto_approved, actor='system'

STEP 6b — HOLD FLAGGED ROWS
  → rows where risk_level='review' → remain in gds_staging_experiments
  → status='pending', awaiting human decision
        │
        ▼
STEP 7 — HUMAN REVIEW (HITL Console)
  Reviewer sees: EC50, Hill slope, R², Emax, dose-response curve, curve quality
  Actions:
    approve  → promote to gds_experiments + audit log
    exclude  → remove from staging + audit log (not a production row)
    reject   → mark rejected + audit log
  [Review Resolution Agent available for plain-English batch decisions]
        │
        ▼
STEP 8 — POST-COMMIT CLEANUP
  purge_committed_sources()
  → hard-delete from ABASE ONLY scientists where ALL compounds are live in GDS
  → scientists with any pending rows remain in ABASE (migration_status='migrating')
        │
        ▼
STEP 9 — VERIFICATION AGENT
  Mandatory tools (always run):
    - reconcile_row_counts()        source vs staged vs live vs removed
    - check_r2_thresholds()         independent re-check of curve quality
    - compare_staging_production()  spot-check values match
  Discretionary tools (LLM decides):
    - per_scientist_breakdown()
    - check_audit_completeness()
    - verify_upsert_keys()
    - check_null_columns()
    - check_curve_data_integrity()
  OUTPUT: enterprise report ending in "Overall: PASS" or "NEEDS REVIEW"
```

### 4.2 Orchestration Modes

| Mode | Activation | Behaviour |
|---|---|---|
| Deterministic pipeline | `ORCHESTRATOR_AGENT=false` (default) | Fixed linear sequence. Fast, predictable, no extra LLM turns. |
| Orchestrator Agent | `ORCHESTRATOR_AGENT=true` | LLM drives each step. Enforces backup requirement. Stricter escalation. |

Both modes share `pipeline_helpers.py` — writes are identical regardless of mode.

---

## 5. Agent Workflows

### 5.1 Migration Agent — Tool Loop Detail

```
INITIAL MESSAGE: "Migrate all records from SOURCE to TARGET. trace_id: {uuid}"

TURN 1:
  Gemini reads system prompt + initial message
  → calls discover_source_schema()
  ← receives: {tables: {experiments: {columns: [...], row_count: 240}, users: {...}}}

TURN 2:
  Gemini reads schema result
  → calls discover_target_schema()
  ← receives: {tables: {gds_staging_experiments, gds_users, gds_experiments, ...}}

TURN 3:
  Gemini sees concentration_um in source schema
  → calls sample_source_data(table_name="experiments", limit=10)
  ← receives: {rows: [{raw_value: 88000, well_type: "neg_ctrl", ...}, ...]}

TURN 4:
  Gemini sees neg_ctrl/pos_ctrl pattern → knows this is dose-response data
  → calls write_and_test_mapping(script="...100 lines of pandas+scipy code...")
  script contains:
    - experiments.merge(users) JOIN
    - groupby(scientist_name, compound_id)
    - neg_ctrl/pos_ctrl normalization → % inhibition
    - Hill equation curve_fit
    - R² computation
    - per-point residual classification
    - risk_level = 'review' if r2 < 0.90 else 'auto'
  ← receives: {status: success, rows: 20, auto_rows: 12, review_rows: 8}

TURN 5:
  Gemini sees transform succeeded
  → calls store_promotion_config(config_json="...JSON mapping...")
  ← receives: {status: success, note: "your job is done"}

TURN 6:
  Gemini writes summary, no tool calls
  EARLY-STOP GUARD: cleaned_df exists AND promotion_config exists → break

RESULT: cleaned_records (20 rows) + promotion_config (JSON)
```

### 5.2 Self-Repair Loop

When `write_and_test_mapping` returns an error:

```
TURN N:
  Gemini calls write_and_test_mapping(script=<first attempt>)
  ← {status: error, error: "NameError: name 'curve_fit' is not in scope"}

TURN N+1:
  Gemini reads traceback
  → calls write_and_test_mapping(script=<fixed script — imports corrected>)
  ← {status: success, rows: 20}
```

Failure cap: if any single tool errors 5 times (`MAX_TOOL_FAILURES=5`), the agent aborts with a `RuntimeError`. Run is safe to retry.

### 5.3 Verification Agent — Mandatory Tool Battery

The Verification Agent must call these tools in every run:

```
MANDATORY (always):
  reconcile_row_counts()       — source count = live + removed + pending
  check_r2_thresholds()        — independent recompute, compare to agent's classification
  compare_staging_production() — spot-check 5 random rows: staging JSONB = GDS columns

DISCRETIONARY (LLM decides based on findings):
  per_scientist_breakdown()
  check_audit_completeness()
  verify_upsert_keys()
  check_null_columns()
  check_curve_data_integrity()
```

Guard: if mandatory tools are not all called, the agent loop injects a reminder and continues. LLM never computes a number — all figures come from SQL tool results.

---

## 6. Data Flow

### 6.1 Source Data Shape (ABASE)
240 rows — one per well reading:

```
experiments table:
  id             UUID
  user_id        UUID → FK to users.id
  compound_id    TEXT  (e.g. "CPD-001")
  assay_type     TEXT
  plate_barcode  TEXT
  well_position  TEXT  (e.g. "A03")
  well_type      TEXT  ('sample' | 'neg_ctrl' | 'pos_ctrl')
  concentration_um FLOAT  (null for control wells)
  raw_value      FLOAT  (RFU — raw fluorescence units)
```

### 6.2 In-Memory Transform (Agent)

Agent groups 240 rows → 20 compound rows:

```
For each (scientist, compound):
  samples    = wells where well_type = 'sample'     (10 wells)
  neg_ctrls  = wells where well_type = 'neg_ctrl'   (1 well)
  pos_ctrls  = wells where well_type = 'pos_ctrl'   (1 well)

  neg_ctrl_mean = mean(neg_ctrl raw_values)   ← 0% inhibition baseline
  pos_ctrl_mean = mean(pos_ctrl raw_values)   ← 100% inhibition baseline

  normalized_response = (neg_ctrl_mean - raw_value) / (neg_ctrl_mean - pos_ctrl_mean) × 100
                        clipped to [0, 100]

  Hill equation fit:
    y = Emax / (1 + (EC50 / c)^n)
    fit params: ec50_um, hill_slope (n), signal (Emax)

  R² = 1 - SS_residuals / SS_total

  risk_level = 'review' if R² < 0.90 else 'auto'
  curve_quality = 'excellent' | 'good' | 'fair' | 'poor'

  per-point classification:
    |residual| > 3σ → 'critical'
    |residual| > 2σ → 'masked'
    otherwise       → 'valid'
```

### 6.3 Staging Shape (GDS — transient)
20 rows in `gds_staging_experiments`:

```
  id             UUID
  trace_id       UUID
  status         TEXT  ('pending' | 'approved' | 'excluded' | 'rejected')
  risk_level     TEXT  ('auto' | 'review')
  data           JSONB  ← entire compound row as JSON blob
  created_at     TIMESTAMPTZ
```

### 6.4 Production Shape (GDS — immutable)
After promotion, in `gds_experiments`:

```
  experiment_id           UUID (PK)
  gds_user_id             UUID → FK to gds_users
  compound_id             TEXT
  ec50_um                 FLOAT
  hill_slope              FLOAT
  r_squared               FLOAT
  signal                  FLOAT  (Emax)
  curve_quality           TEXT
  num_concentration_points INT
  assay_type              TEXT
  plate_barcode           TEXT
  curve_data              JSONB  ([{well_position, conc_um, response, quality}])
  neg_ctrl_mean           FLOAT
  pos_ctrl_mean           FLOAT
  source_experiment_id    TEXT   ← FK back to ABASE for traceability
  trace_id                UUID
  approved_by             TEXT
  approved_at             TIMESTAMPTZ
  UNIQUE(gds_user_id, compound_id)   ← prevents duplicates on re-migration
```

---

## 7. The Promotion Config Contract

The `promotion_config` JSON is the architectural glue between the AI zone and the infrastructure zone.

### 7.1 Structure

```json
{
  "staging_table": "gds_staging_experiments",
  "entity_table": {
    "name": "gds_users",
    "column_map": { "scientist_name": "name" },
    "upsert_key": "name",
    "pk": "gds_user_id"
  },
  "records_table": {
    "name": "gds_experiments",
    "column_map": {
      "signal":                   "signal",
      "compound_id":              "compound_id",
      "ec50_um":                  "ec50_um",
      "hill_slope":               "hill_slope",
      "r_squared":                "r_squared",
      "curve_quality":            "curve_quality",
      "num_concentration_points": "num_concentration_points",
      "assay_type":               "assay_type",
      "source_experiment_id":     "source_experiment_id",
      "plate_barcode":            "plate_barcode",
      "curve_data":               "curve_data",
      "neg_ctrl_mean":            "neg_ctrl_mean",
      "pos_ctrl_mean":            "pos_ctrl_mean"
    },
    "fk_column": "gds_user_id",
    "upsert_keys": ["gds_user_id", "compound_id"]
  },
  "anomaly_thresholds": {
    "r_squared": { "low": 0.0, "high": 0.9, "method": "threshold" }
  }
}
```

### 7.2 Safety Guards

| Guard | Implementation |
|---|---|
| Identifier validation | `validate_identifier()` checks every table/column name before SQL use |
| Upsert key verification | `_resolve_conflict_target()` validates proposed keys against real `pg_constraint` catalog — agent proposes, infrastructure guarantees |
| Resilient entity mapping | `_promote_rows()` filters `entity_table.column_map` to only keys present in staging data — agent-mapped column names that differ from actual staged keys are silently skipped rather than failing hard |
| JSONB serialization | List/dict values (e.g. `curve_data`) are serialized to JSON strings and cast as `::jsonb` in the INSERT — prevents type mismatch on Supabase |
| Supabase-compatible introspection | Uses `pg_catalog` (not `information_schema`) — `information_schema.key_column_usage` returns empty on Supabase pgBouncer sessions |

---

## 8. Human-in-the-Loop Process

### 8.1 HITL Gates

**Gate 1 — Mapping Review (v2, not yet built)**
Triggered for unknown/untrusted schemas. Human reviews the AI's `promotion_config` draft before any data moves. The agent generates the mapping; a human approves it.

**Gate 2 — Row Review (built)**
All rows where `risk_level='review'` are held in staging. Human reviews in the HITL Console.

### 8.2 Escalation Rules

| Condition | Result |
|---|---|
| Critic returns APPROVE | Auto-promote `risk_level='auto'` rows, hold `review` rows |
| Critic returns FLAG + error finding | ALL rows forced to `review`, auto-promote skipped |
| Critic returns FLAG + warning/info only | Auto-promote proceeds, warnings surfaced in UI |
| Critic unavailable (LLM down) | Fail-open: auto-promote proceeds (default) |
| Critic unavailable, fail-closed mode | All rows forced to `review` (one-line config change) |

### 8.3 HITL Console Actions

| Action | Effect | Audit Log Entry |
|---|---|---|
| Approve | Promote row to `gds_experiments` | `hitl_approved`, actor, timestamp |
| Exclude | Remove from staging, not promoted | `hitl_excluded`, actor, timestamp |
| Reject | Mark rejected, stays in staging | `hitl_rejected`, actor, timestamp |
| Approve All | Batch approve all pending rows | `hitl_approved` per row |
| Exclude All | Batch exclude all pending rows | `hitl_excluded` per row |

### 8.4 Review Resolution Agent
Accepts plain-English messages:
- *"Approve all compounds for Dr. Smith"* → batch approve
- *"Exclude CPD-007 — it's a known assay artifact"* → targeted exclude
- Writes attributed audit events with actor + timestamp

---

## 9. Hallucination Defense Stack

The LLM can return confident, wrong output. The defense is not to prevent hallucination — it is to make every LLM claim falsifiable before data reaches production.

| Layer | LLM Claim | Deterministic Check | On Failure |
|---|---|---|---|
| 1 | "This transform script is correct" | Script executed against real source data in-memory (`exec()`) | Self-repair loop, 5 failures → abort |
| 2 | "Map to column X" | `validate_identifier()` confirms real, safe identifier | `ValueError` → no write |
| 3 | "Use these upsert keys" | `_resolve_conflict_target()` matches against real `pg_constraint` | Falls back to real UNIQUE constraint |
| 4 | "These source fields exist" | `_promote_rows()` checks every `column_map` key in staging data | `RuntimeError` with expected vs actual keys |
| 5 | "This row is anomalous" | Risk computed by pandas/numpy (mean±2σ or R² threshold) — not LLM opinion | N/A — LLM never classifies rows |
| 6 | "This mapping is sound" | Critic Agent audits `promotion_config` → APPROVE / FLAG | FLAG forces HITL |
| 7 | "Migration succeeded" | Verification Agent independently recomputes all thresholds | Report flags NEEDS REVIEW |

**Key invariant:** The LLM never reports a number it computed itself. All figures in the verification report come from deterministic SQL tool results.

---

## 10. Failure Modes & Recovery

### 10.1 LLM Unavailable

**Model failover chain (automatic):**
```
gemini-2.5-flash → gemini-2.0-flash → gemini-2.5-flash-lite
```
On transient error (429/503/UNAVAILABLE): fail over, then back off (2s → 4s → 8s), retry up to 4 rounds.
On non-transient error (real 400): surface immediately — genuine bugs never masked.

| Component | LLM Down | Net Effect |
|---|---|---|
| Migration Agent | Abort → HTTP 500, zero DB writes | Source untouched. Safe to re-run. |
| Mapping Critic | Fail-open: proceed without verdict | Auto-promote unguarded (known risk, documented) |
| Verification Agent | Fallback to deterministic report_agent | Report still produced from real DB numbers |

### 10.2 Backend Process Dies Mid-Migration

- Agent is read-only — no partial writes from the agent
- ABASE source is marked `migration_status='migrating'` (not deleted)
- Any uncommitted GDS transaction rolls back
- UNLOGGED staging table is transient by design (data loss on crash is intentional)
- **Recovery:** re-run with same or new `trace_id`. Promotion is idempotent — `ON CONFLICT (gds_user_id, compound_id) DO UPDATE` prevents duplicates.

### 10.3 HTTP Request Timeout

- Synchronous path (`POST /api/agent/run`) can exceed 30-60s gateway timeout on long runs
- **Solution:** `POST /api/agent/run/async` returns `{trace_id, status: queued}` immediately (HTTP 202)
- Poll: `GET /api/agent/jobs/{trace_id}` for status and result

### 10.4 Database Unreachable

- `asyncpg` pool `acquire()` fails → request errors cleanly, no partial write
- **Recovery:** `/health` endpoint should verify both pools so load balancer stops routing to broken instance

### 10.5 Agent Stuck in Loop

- `MAX_TURNS = 15` — hard ceiling on Migration Agent turns
- `MAX_TOOL_FAILURES = 5` — if any single tool errors 5 times, agent aborts
- Early-stop guard — injects a reminder if agent tries to stop before completing mandatory tools
- Result: agent either completes or fails loudly with `RuntimeError` — never loops indefinitely

### 10.6 Structurally Valid but Semantically Wrong Mapping

The one gap deterministic checks cannot fully close:

- LLM maps source column to the wrong but type-compatible target column
- All identifier and type checks pass
- **Defense:** Mapping Critic (Layer 6) and Gate 1 human review (v2)
- **Mitigation today:** Critic error-severity finding forces all rows to human HITL before promotion

---

## 11. Database Schema

### 11.1 ABASE (Source)
```sql
users (
  id               UUID PRIMARY KEY,
  name             TEXT NOT NULL,
  email            TEXT,
  department       TEXT,
  role             TEXT,
  migration_status TEXT DEFAULT 'pending'  -- 'pending' | 'migrating' | 'migrated'
)

experiments (
  id               UUID PRIMARY KEY,
  user_id          UUID REFERENCES users(id),
  compound_id      TEXT,
  assay_type       TEXT,
  plate_barcode    TEXT,
  well_position    TEXT,
  well_type        TEXT,  -- 'sample' | 'neg_ctrl' | 'pos_ctrl'
  concentration_um FLOAT,
  raw_value        FLOAT
)
```

### 11.2 GDS (Target)
```sql
gds_users (
  gds_user_id  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name         TEXT UNIQUE NOT NULL,
  created_at   TIMESTAMPTZ DEFAULT NOW()
)

gds_staging_experiments (  -- UNLOGGED: fast transient buffer
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  trace_id     UUID NOT NULL,
  status       TEXT DEFAULT 'pending',
  risk_level   TEXT,
  data         JSONB NOT NULL,
  created_at   TIMESTAMPTZ DEFAULT NOW()
)

gds_experiments (  -- immutable production table
  experiment_id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  gds_user_id                UUID REFERENCES gds_users(gds_user_id),
  trace_id                   UUID,
  compound_id                TEXT,
  ec50_um                    FLOAT,
  hill_slope                 FLOAT,
  r_squared                  FLOAT,
  signal                     FLOAT,
  curve_quality              TEXT,
  num_concentration_points   INT,
  assay_type                 TEXT,
  plate_barcode              TEXT,
  curve_data                 JSONB,
  neg_ctrl_mean              FLOAT,
  pos_ctrl_mean              FLOAT,
  source_experiment_id       TEXT,
  approved_by                TEXT,
  approved_at                TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(gds_user_id, compound_id)
)

migration_plans (
  trace_id          UUID PRIMARY KEY,
  promotion_config  JSONB NOT NULL,
  created_at        TIMESTAMPTZ DEFAULT NOW()
)

migration_audit_log (  -- append-only
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  trace_id    UUID,
  event_type  TEXT,  -- hitl_approved | hitl_excluded | hitl_rejected | hitl_auto_approved
  actor       TEXT,
  row_id      UUID,
  details     JSONB,
  created_at  TIMESTAMPTZ DEFAULT NOW()
)

migration_source_backups (
  trace_id     UUID PRIMARY KEY,
  provider     TEXT,
  snapshot_id  TEXT,
  source_host  TEXT,
  status       TEXT,
  metadata     JSONB,
  created_at   TIMESTAMPTZ DEFAULT NOW()
)

migration_jobs (
  trace_id    UUID PRIMARY KEY,
  status      TEXT,  -- queued | running | succeeded | failed
  result      JSONB,
  created_at  TIMESTAMPTZ DEFAULT NOW(),
  updated_at  TIMESTAMPTZ DEFAULT NOW()
)
```

---

## 12. API Reference

### 12.1 Migration Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/agent/run` | Synchronous migration run. Returns full result when complete. |
| `POST` | `/api/agent/run/async` | Async migration run. Returns `{trace_id, status: queued}` immediately. |
| `GET` | `/api/agent/jobs/{trace_id}` | Poll async job status and result. |

**Request body:**
```json
{
  "source_url": "postgresql://...",  // optional, defaults to ABASE_DATABASE_URL
  "target_url": "postgresql://...",  // optional, defaults to GDS_DATABASE_URL
  "initiated_by": "user@company.com"
}
```

### 12.2 HITL Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/migrate/staging/{trace_id}` | List pending review rows for a run |
| `POST` | `/api/migrate/approve/{row_id}` | Approve a single row → promotes to production |
| `POST` | `/api/migrate/reject/{row_id}` | Reject a single row |
| `POST` | `/api/migrate/rollback/{trace_id}` | Roll back an entire migration run |
| `GET` | `/api/migrate/restore-point/{trace_id}` | Get backup metadata for a run |

### 12.3 Read Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/abase/scientists` | List ABASE scientists with migration status |
| `GET` | `/api/abase/experiments/{user_id}` | Get experiments for a scientist |
| `GET` | `/api/gds/scientists` | List GDS scientists with `avg_ec50`, `avg_r_squared`, experiment count |
| `GET` | `/api/gds/experiments/{user_id}` | Get compound experiments for a GDS scientist — includes `ec50_um`, `hill_slope`, `r_squared`, `curve_quality`, `curve_data` |
| `GET` | `/api/gds/heatmap/{user_id}` | 96-well plate heatmap data |
| `GET` | `/api/report/{trace_id}` | Verification report for a completed run |
| `GET` | `/health` | Backend health check (both DB pools + LLM) |

---

## 13. Security & Compliance

### 13.1 Data Security

| Control | Implementation |
|---|---|
| Agents never write to DB | Enforced architecturally — no write tools in any agent |
| Credentials | Stored in `.env`, percent-encoded for special characters, decoded at connection time only |
| SQL injection prevention | All identifiers validated via `validate_identifier()` before use in SQL |
| Connection encryption | `ssl="require"` on all asyncpg connections |
| No secrets in logs | Connection strings logged as host-only (`url.split("@")[-1]`) |

### 13.2 Audit Trail

Every data write is attributed:
```
migration_audit_log:
  event_type   — what happened (auto_approved / hitl_approved / hitl_excluded / hitl_rejected)
  actor        — who did it ('system' for auto, user email for human)
  row_id       — which staging row
  trace_id     — which migration run
  created_at   — exact timestamp
```

### 13.3 Data Integrity

- Source never deleted until target confirmed: `migration_status='migrating'` set before any write; `purge_committed_sources()` called only after verification
- Production records immutable: `UNIQUE(gds_user_id, compound_id)` prevents overwrites
- Staging is transient: `UNLOGGED` table, data loss on crash intentional and documented
- Re-migration is idempotent: `ON CONFLICT DO UPDATE` on all promotion upserts

### 13.4 Compliance Features

- Full column exclusion proof — agent cannot map columns that don't exist in the target schema. `promotion_config` is stored per run as evidence of what was and was not migrated.
- Rollback capability — `POST /api/migrate/rollback/{trace_id}` removes staging rows; source remains in `migration_status='migrating'` until manually cleared
- Restore point — pre-migration snapshot metadata accessible via `GET /api/migrate/restore-point/{trace_id}`

---

## 14. Operational Procedures

### 14.1 Pre-Demo Reset
Run before every demo session to restore clean state:
```bash
cd backend
python scripts/reset_demo.py
# Expected: ABASE → 20 scientists, 240 wells | GDS → 0 users, 0 experiments, 0 staging rows
# Status: READY FOR DEMO ✓
```

### 14.2 Running a Migration
```bash
# Synchronous (development / short runs)
curl -X POST http://localhost:8001/api/agent/run \
  -H "Content-Type: application/json" \
  -d '{"initiated_by": "admin@company.com"}'

# Async (production / long runs)
curl -X POST http://localhost:8001/api/agent/run/async \
  -H "Content-Type: application/json" \
  -d '{"initiated_by": "admin@company.com"}'
# → returns {trace_id: "...", status: "queued"}

# Poll for completion
curl http://localhost:8001/api/agent/jobs/{trace_id}
```

### 14.3 Checking Database State
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
            print(f"GDS → users={await conn.fetchval('SELECT COUNT(*) FROM gds_users')}, "
                  f"experiments={await conn.fetchval('SELECT COUNT(*) FROM gds_experiments')}, "
                  f"staging={await conn.fetchval('SELECT COUNT(*) FROM gds_staging_experiments')}")
        await conn.close()

asyncio.run(check())
EOF
```

### 14.4 Monitoring Agent Reasoning
```bash
tail -f backend/logs/backend.log | grep "AGENT"
```

Log entries follow the pattern:
```
AGENT [TOOL_CALL:discover_source_schema] {...}
AGENT [TOOL_RESULT:discover_source_schema] {status: success, ...}
AGENT [TOOL_CALL:write_and_test_mapping] {script: "..."}
AGENT [EARLY_STOP_GUARD] {missing: ["store_promotion_config"]}
AGENT [AGENT_DONE] {message: "Migration complete. 20 compounds processed..."}
```

### 14.5 Rollback a Migration
```bash
curl -X POST http://localhost:8001/api/migrate/rollback/{trace_id}
```
Removes staging rows for the run. Source remains in `migration_status='migrating'` — manually reset if needed:
```sql
UPDATE users SET migration_status = 'pending' WHERE migration_status = 'migrating';
```

### 14.6 Gemini API Key Rotation
1. Go to https://aistudio.google.com/app/apikey
2. Create new key (navigating away from this page invalidates it — copy immediately)
3. Update `backend/.env` → `GEMINI_API_KEY=<new key>`
4. Restart backend: `python -m uvicorn app.main:app --reload --port 8001`

---

## 15. Deployment Topology

### 15.1 Pilot / Demo Deployment

| Component | Platform | Notes |
|---|---|---|
| FastAPI backend | Render / Railway / Fly.io / Cloud Run | Stateless. `GET /health` for probe. |
| HITL Console frontend | Vercel | `NEXT_PUBLIC_API_URL` → backend URL |
| ABASE Viewer frontend | Vercel | Same env var |
| GDS Viewer frontend | Vercel | Same env var |
| ABASE database | Supabase us-west-2 | Existing managed instance |
| GDS database | Supabase us-east-2 | Existing managed instance |

### 15.2 Environment Variables

```bash
# Required
ABASE_DATABASE_URL=postgresql://user:pass%40word@host:5432/db
GDS_DATABASE_URL=postgresql://user:password@host:5432/db
GEMINI_API_KEY=AIza...

# Orchestration
ORCHESTRATOR_AGENT=true            # 'true' for agentic driver, 'false' for fixed pipeline

# Backup
BACKUP_PROVIDER=pg_dump            # pg_dump | aws_rds | supabase | webhook | none
BACKUP_POLL_INTERVAL_SEC=15
BACKUP_POLL_TIMEOUT_SEC=600

# CORS (production — list all frontend origins)
FRONTEND_URL=https://your-hitl-console.vercel.app
```

### 15.3 Production Hardening Checklist

- [ ] Run migration async (`/api/agent/run/async`) — synchronous path will timeout under load
- [ ] Set `BACKUP_PROVIDER` to a real provider (not `none`)
- [ ] Decide critic fail-open vs fail-closed policy (FAILURE-MODES.md §3b)
- [ ] Pin `numpy` explicitly in `requirements.txt`
- [ ] Allow all three frontend origins in CORS config (`main.py`)
- [ ] Set connection pool `max_size` to stay within Supabase connection budget
- [ ] Add `/health` check that verifies both DB pools
- [ ] Rotate `GEMINI_API_KEY` into a real secret manager (not `.env` in repo)
- [ ] Enable `ORCHESTRATOR_AGENT=true` — stricter backup enforcement for production

---

## 16. Known Limitations & Roadmap

### 16.1 Current Limitations

| Limitation | Detail |
|---|---|
| PostgreSQL only | Source and target must both be PostgreSQL. MySQL, MongoDB, flat files not supported. |
| Batch migration | Not a real-time sync or CDC (change data capture) tool |
| Gate 1 not built | Mapping review UI is a v2 feature. Unknown schemas go straight to staging today. |
| Background task is in-process | Async runs are lost on backend restart. Production needs a durable queue. |
| Critic is fail-open | Gemini down during critic step = auto-promote proceeds unguarded |
| Single-instance async | Multiple replicas need coordination via `migration_jobs` table (partially implemented) |

### 16.2 Roadmap

| Version | Feature |
|---|---|
| v1.1 | Gate 1 mapping review UI — human approves AI mapping draft |
| v1.2 | Durable async queue — runs survive backend restarts, multi-replica safe |
| v1.3 | MySQL and flat file (CSV) sources |
| v2.0 | Scheduling — recurring migrations, not just one-time runs |
| v2.1 | Enterprise SSO + role-based access control |
| v2.2 | Multi-tenant HITL queue — all pending reviews across all migrations in one console |
| v3.0 | On-premise LLM option (for air-gapped enterprise environments) |

---

*Document maintained by the Migration Agent engineering team.*
*For the architecture diagram and design rationale, see `ARCHITECTURE.md`.*
*For failure mode details, see `docs/FAILURE-MODES.md`.*
*For use case examples, see `docs/USE_CASES.md`.*
