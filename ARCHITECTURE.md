# Architecture

This document describes how the Migration Agent platform is designed and why.

---

## 1. Design principles

1. **Agents reason; code computes and writes.** Every database write and every exact computation
   is done by deterministic infrastructure code. The LLM agents decide *what* to do and interpret
   results — they never touch the database directly. This is the foundation of the system's
   auditability.
2. **Read-only agents.** No agent is given a write tool. The Migration and Verification agents are
   read-only/in-memory; the Review agent's *actions* are executed by deterministic promotion code.
3. **`promotion_config` is the glue.** The Migration Agent *outputs* a JSON mapping describing how
   intermediate columns map to the target tables. Infrastructure *reads it back* to promote data
   generically — so the same code path works for any schema.
4. **No hardcoded database identifiers.** Table names, column names, and FK relationships are
   discovered at runtime from `information_schema` or read from `promotion_config`.
5. **Schema-agnostic.** Pass any `source_url` + `target_url`; ABASE → GDS is just the default.

---

## 2. End-to-end workflow

Top to bottom. The label in each box says **who** acts — the AI proposes (read-only), deterministic
code performs every write, and humans handle only the uncertain rows.

```
   LEGEND:  🟢 AI agent (read-only)   ⚪ Deterministic code (the only writer)
            🟡 Single LLM call        👤 Human

┌────────────────────────────────────────────────────────────────────────┐
│  SOURCE DB (ABASE)                          TARGET DB (GDS)              │
│  • users / experiments                      • staging (transient)        │
│  • raw RFU wells, concentrations            • production (immutable)     │
│  • neg_ctrl / pos_ctrl wells                • EC50, Hill slope, R²       │
└────────────────────────────────────────────────────────────────────────┘
        │ read-only                                        ▲ all writes
        ▼                                                  │ happen here
┌────────────────────────────────────────────────────────────────────────┐
│  1.  TRIGGER                                                    ⚪        │
│      POST /api/agent/run   →   assign trace_id                           │
└──────────────────────────────────┬───────────────────────────────────────┘
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│  1b. SOURCE BACKUP  (before any change)                        ⚪        │
│      pg_dump / cloud snapshot of the source DB                           │
│      provider: pg_dump (local) / aws_rds / supabase / webhook            │
│      record snapshot_id → migration_source_backups + SQLite log          │
└──────────────────────────────────┬───────────────────────────────────────┘
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│  2.  MIGRATION AGENT                                            🟢        │
│      discover schemas (information_schema)                               │
│      sample real rows to understand column semantics                     │
│      write pandas+scipy transform script, self-repair on error           │
│      ┌─ transform steps: ──────────────────────────────────────────┐    │
│      │  • separate neg_ctrl / pos_ctrl / sample wells              │    │
│      │  • compute neg_ctrl_mean and pos_ctrl_mean (RFU)            │    │
│      │  • normalize: % inhibition = (neg − raw) / (neg − pos) × 100│    │
│      │  • fit Hill equation: y = Emax / (1 + (EC50/c)^n)          │    │
│      │  • compute EC50, Hill slope, R², Emax per compound          │    │
│      │  • classify each point: valid / critical by residual        │    │
│      └────────────────────────────────────────────────────────────┘    │
│      screen each compound: R² < 0.90 → risk_level='review'              │
│                             R² ≥ 0.90 → risk_level='auto'               │
│                                                                          │
│      OUTPUT:  cleaned_records  +  promotion_config (JSON plan)           │
│      ── no database writes in this box ──                                │
└──────────────────────────────────┬───────────────────────────────────────┘
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│  3.  WRITE TO STAGING                                          ⚪        │
│      all rows → gds_staging_experiments  (status = pending)              │
└──────────────────────────────────┬───────────────────────────────────────┘
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│  4.  MARK SOURCE 'migrating'   +   STORE PLAN                  ⚪        │
│      ABASE users.migration_status = 'migrating'  (NO delete yet)         │
│      promotion_config → migration_plans                                  │
└──────────────────────────────────┬───────────────────────────────────────┘
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│  5.  MAPPING CRITIC                                            🟡        │
│      audits the column mapping  →  verdict: APPROVE  or  FLAG            │
└───────────────┬───────────────────────────────────┬──────────────────────┘
                │ FLAG + error-severity finding       │ APPROVE (or warn/info only)
                ▼                                     ▼
┌──────────────────────────────┐      ┌──────────────────────────────────┐
│  5a. FORCE ALL → review   ⚪ │      │  6.  SPLIT BY risk_level      ⚪  │
│      (auto-promote skipped)  │      │                                   │
└──────────────┬───────────────┘      │   auto ───────▶ AUTO-PROMOTE      │
               │                       │                 → gds_experiments │
               │                       │                                   │
               └───────────────────────┤   review ─────▶ HOLD in staging   │
                                        └──────────────┬────────────────────┘
                                                       ▼
┌────────────────────────────────────────────────────────────────────────┐
│  7.  HUMAN REVIEW  (only the flagged / uncertain rows)        👤        │
│      HITL console shows: EC50, Hill slope, R², dose–response curve      │
│      approve / exclude / reject     [+ Review Resolution Agent 🟢]       │
│      approved → gds_experiments                                          │
└──────────────────────────────────┬───────────────────────────────────────┘
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│  8.  POST-COMMIT CLEANUP                                       ⚪        │
│      hard-delete source ONLY for scientists fully live in GDS           │
│      (everyone else stays 'migrating' until resolved)                    │
└──────────────────────────────────┬───────────────────────────────────────┘
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│  9.  VERIFICATION AGENT                                        🟢        │
│      recompute R² thresholds · reconcile staged = live + removed + pending│
│      compare staging vs production values · per-scientist breakdown      │
│                                                                          │
│      REPORT:  Overall PASS   or   NEEDS REVIEW                          │
└────────────────────────────────────────────────────────────────────────┘
```

Three properties the diagram makes explicit:
1. **The only AI that touches the data (box 2) is read-only.** Everything that *writes* is
   deterministic code — that is the trust boundary.
2. **The plan flows down, not the AI.** Box 2 emits `promotion_config`; every box below executes it.
3. **Two safety gates + an independent auditor:** the Critic can divert everything to humans, humans
   handle only the uncertain rows, and a separate agent grades the result.

---

## 3. Component taxonomy

The word "agent" is used precisely. There are three categories:

### Agentic — true tool-calling agents
An LLM in a loop that chooses which tools to call until a goal is met.

| Agent | File | Tools | Notes |
|---|---|---|---|
| **Migration Agent** | `app/agents/migration_agent.py` | 5 | Discovers schemas, samples data, writes & **self-repairs** a pandas+scipy transform (Hill fit, RFU normalization), screens by R², emits `promotion_config` |
| **Orchestrator Agent** *(opt-in)* | `app/agents/orchestrator_agent.py` | 8 | LLM-driven pipeline driver. Reasons step-by-step, **will not migrate without a confirmed backup**, escalates all rows to HITL on error-severity critic findings. Enabled with `ORCHESTRATOR_AGENT=true` |
| **Review Resolution Agent** | `app/agents/review_agent.py` | 5 | NL message → approve/exclude wells; writes attributed audit events |
| **Verification Agent** | `app/agents/verification_agent.py` | 8 (read-only) | Mandatory-tool battery + discretionary investigation; independently re-checks the migration's R² classification |

### LLM-powered but not agentic — single shot
One LLM call, no loop, no tools.

| Component | File | Role |
|---|---|---|
| **Mapping Critic** | `app/agents/critic_agent.py` | Reviews `promotion_config` before promotion → `APPROVE`/`FLAG` with per-finding severity |
| **Report (fallback)** | `app/agents/report_agent.py` | Deterministic fallback if the Verification Agent fails |

### Deterministic code — no LLM

| Area | File(s) |
|---|---|
| Pipeline entry point | `app/api/agent.py` — fixed pipeline (default) or delegates to Orchestrator Agent |
| Shared pipeline steps | `app/api/pipeline_helpers.py` — `write_to_staging`, `store_migration_plan`, `mark_source_migrating`, `force_all_to_review`, `purge_committed_sources` |
| Source backup | `app/core/backup.py` (provider snapshot + poll), `app/core/backup_store.py` (SQLite metadata log) |
| Writes & promotion | `app/api/migration.py` — `_promote_rows`, approve/reject, upsert-key guard, restore-point lookup, audit log |
| Read APIs | `app/api/abase.py`, `app/api/gds.py`, `app/api/report.py`, `app/api/reviewer.py`, `app/api/critic.py` |
| Plumbing | `app/connectors/*` (async pools), `app/core/llm.py` (Gemini client + failover), `app/core/mapping.py` (validation, coercion) |

---

## 4. Dual-database setup

### Source — ABASE (legacy lab system)
- `users` — researchers, with `migration_status` field
- `experiments` — one row per well reading:
  - `user_id`, `compound_id`, `assay_type`, `plate_barcode`
  - `well_position` (e.g. `A03`), `well_type` (`sample` / `neg_ctrl` / `pos_ctrl`)
  - `concentration_um` — concentration in µM
  - `raw_value` — raw fluorescence reading in RFU

### Target — GDS (drug-screening platform)
- `gds_users` — scientists by name, UUID primary key, unique on `name`
- `gds_experiments` — one row per compound per scientist, immutable once approved:
  - `ec50_um` — fitted EC50 in µM (Hill equation)
  - `hill_slope` — Hill coefficient n
  - `r_squared` — goodness-of-fit R²
  - `signal` — Emax (% inhibition at saturation)
  - `curve_quality` — `excellent` / `good` / `fair` / `poor` (derived from R²)
  - `num_concentration_points` — number of sample wells used in the fit
  - `assay_type` — inherited from source
  - `plate_barcode` — plate identifier
  - `curve_data` — JSONB array of `{well_position, conc_um, response, quality}` per point
  - `neg_ctrl_mean` — mean DMSO fluorescence (RFU); defines the 0% inhibition baseline
  - `pos_ctrl_mean` — mean reference inhibitor fluorescence (RFU); defines the 100% baseline
  - `source_experiment_id` — FK back to ABASE for traceability
  - `UNIQUE(gds_user_id, compound_id)` — prevents duplicates on re-migration
- `gds_staging_experiments` — UNLOGGED transient buffer; data loss on crash is intentional
- `migration_plans` — stores each run's `promotion_config` per `trace_id`
- `migration_audit_log` — append-only attributed audit trail
- `migration_source_backups` — restore-point metadata per run
- `migration_jobs` — async job tracking

Connection strings live in `backend/.env`. Passwords with special characters are percent-encoded
and decoded by the shared `_create_pool()`. Pools are async (`asyncpg`), managed by the FastAPI
lifespan.

---

## 4b. Pre-migration backup (restore point)

Before any change, `_execute_migration()` calls `trigger_backup()` to snapshot the source DB.
This is enterprise infrastructure, not agent logic — the agent never reads or copies data rows.

- **Providers** (`BACKUP_PROVIDER` env var): `pg_dump` (local SQL dump, default for dev),
  `aws_rds` (RDS `CreateDBSnapshot` via boto3), `supabase` (Management API), `webhook`
  (generic HTTP), or `none` (disabled — logs a warning).
- **Non-blocking in the deterministic pipeline.** If backup is unavailable, the fixed pipeline
  logs a warning and proceeds. The **Orchestrator Agent refuses to migrate without a confirmed
  backup** — a stricter posture for production.
- **Metadata** is persisted both to `migration_source_backups` (Postgres) and a zero-setup local
  SQLite log at `backend/data/backup_log.db`, keyed by `trace_id`.
- **Restore:** `gunzip -c <snapshot>.sql.gz | psql <connection_url>`; or inspect metadata via
  `GET /api/migrate/restore-point/{trace_id}`.

---

## 5. The `promotion_config` contract

The Migration Agent emits this JSON; the promotion code consumes it generically — the same
`_promote_rows()` function works for any schema pair.

Current shape for the ABASE → GDS migration:

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

**Safety guards:**
- Every table/column identifier is validated (`validate_identifier`) before use in SQL.
- The agent proposes `upsert_keys`; `_resolve_conflict_target` validates them against real
  `pg_constraint` catalog entries and falls back to the actual UNIQUE constraint if they don't match.
  **Agent proposes, infrastructure guarantees.**

> **Supabase note:** `_resolve_conflict_target` queries `pg_constraint` / `pg_class` /
> `pg_attribute` rather than `information_schema.key_column_usage`. The `information_schema` view
> returns empty rows on Supabase's pgBouncer pooler sessions, which caused silent fallback to
> unvalidated keys. `pg_catalog` is always accessible and is the correct choice for DDL
> introspection on Supabase.

---

## 6. Curve quality screening (R²-based)

The Migration Agent classifies each compound experiment by its dose–response fit quality:

```
R² ≥ 0.90  →  risk_level = 'auto'    (good fit — auto-promote)
R² < 0.90  →  risk_level = 'review'  (poor fit — hold for human review)

curve_quality label:
  R² ≥ 0.95  →  'excellent'
  R² ≥ 0.90  →  'good'
  R² ≥ 0.80  →  'fair'
  R² < 0.80  →  'poor'
```

The threshold (0.90) is specified in `anomaly_thresholds` inside `promotion_config` — it comes
from the agent's reasoning about the data, not a hardcoded constant.

Individual data points within a curve are also classified by their residual from the fitted line:

```
|residual| > 3σ  →  quality = 'critical'   (excluded from the fit, shown as X on the curve)
otherwise        →  quality = 'valid'       (included)
```

The **Verification Agent independently recomputes** these thresholds and compares its
classification to the Migration Agent's — one agent auditing another.

---

## 7. Human-in-the-loop (HITL)

- **Auto records** (`risk_level='auto'`) → promoted to production immediately, no human needed,
  unless the Mapping Critic flags the run with an error-severity finding (in which case every
  record is forced to `review`).
- **Review records** (`risk_level='review'`) → held in staging. The HITL console shows each
  flagged compound with: EC50, Hill slope, R², Emax, curve quality badge, and a full interactive
  dose–response SVG. A human approves, excludes, or rejects (directly or via the Review
  Resolution Agent).

Every resolution writes an attributed event (`hitl_approved` / `hitl_excluded` / `hitl_rejected`,
with actor and timestamp) to `migration_audit_log`.

**Gate 1 — mapping review** surfaces the *draft column mapping* backed by the Critic's verdict.
Escalation is **severity-based**: auto-promotion is skipped only when the Critic returns `FLAG`
**and** at least one finding is `error`-severity (type mismatch, data-corruption risk). Warning-
and info-level findings are surfaced in the UI but do **not** block auto-promotion. Critic
failure (LLM unavailable) is non-blocking (fail-open; see FAILURE-MODES.md §3b for the
fail-closed hardening option).

---

## 8. Orchestration — two interchangeable drivers

`_execute_migration()` in `app/api/agent.py` has two modes:

- **Default — deterministic pipeline (`ORCHESTRATOR_AGENT=false`).** A fixed, linear sequence:
  backup → ETL → stage → critic → auto-promote → cleanup. Predictable, cheap, correct for a
  known pipeline.
- **Opt-in — Orchestrator Agent (`ORCHESTRATOR_AGENT=true`).** `orchestrator_agent.py` is a
  true tool-calling agent (8 tools, ≤25 turns) that reasons about each step. It **will not
  migrate without a confirmed backup** (`check_backup_status` must return `confirmed=true`),
  escalates all rows to HITL on error-severity critic findings, flags unusual runs (>50% rows
  flagged), and can retry a failed sub-agent once. Both modes call the same sub-agents and
  `pipeline_helpers.py`, so writes are equivalent.

---

## 9. Resilience

`app/core/llm.py` wraps every Gemini call with **model failover**: on a transient error
(429/503/overload) it retries on the next model in the chain before backing off:

```
gemini-2.5-flash → gemini-2.0-flash → gemini-2.5-flash-lite
```

Backoff: 2s → 4s → 8s across up to 4 rounds. A non-transient error on the requested model is
surfaced immediately — genuine bugs are never masked.

---

## 10. Traceability

Every run has a UUID `trace_id` that flows through: agent run → staging rows → `migration_plans`
→ `migration_audit_log` → HITL decisions → the verification report. Use it to audit, inspect, or
roll back a single migration.

---

## 11. GDS viewer — what scientists see after migration

The GDS viewer (`gds-frontend/`, localhost:3002) surfaces the migrated data:

- **96-well plate heatmap** — each cell colored by % inhibition. Columns 11–12 are control wells:
  col 11 = DMSO (neg ctrl, slate), col 12 = reference inhibitor (pos ctrl, violet). Hovering a
  control well shows its RFU mean.
- **Normalization strip** — above each dose–response curve:
  `DMSO baseline · Ref inhibitor · Window` all in RFU, so auditors can verify the assay window.
- **Dose–response SVG** — fitted sigmoid with EC50 marker, data points color-coded by quality
  (valid = blue dot; critical = red X).
- **EC50 metric cards** — EC50, Hill n, R², Emax, number of concentration points.

---

## 12. Tech stack

| Layer | Technology |
|---|---|
| Backend | Python 3.11+, FastAPI, asyncpg |
| AI | Google Gemini (`google-genai` SDK) — gemini-2.5-flash primary |
| Data transform | pandas, numpy, scipy (curve_fit for Hill equation) |
| Frontends | Next.js / React (×3), Tailwind CSS |
| Database | PostgreSQL (Supabase in the reference deployment); SQLite for the local backup log |
| Backup providers | pg_dump (local), AWS RDS (boto3), Supabase Management API, generic webhook |
