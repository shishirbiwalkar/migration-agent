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
│                                             • production (immutable)     │
└────────────────────────────────────────────────────────────────────────┘
        │ read-only                                        ▲ all writes
        ▼                                                  │ happen here
┌────────────────────────────────────────────────────────────────────────┐
│  1.  TRIGGER                                                    ⚪        │
│      POST /api/agent/run   →   assign trace_id                           │
└──────────────────────────────────┬───────────────────────────────────────┘
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│  1b. SOURCE BACKUP  (non-blocking)                             ⚪        │
│      cloud snapshot of the source DB BEFORE any change                   │
│      provider: aws_rds / supabase / webhook / none                       │
│      record snapshot_id → migration_source_backups + SQLite log          │
└──────────────────────────────────┬───────────────────────────────────────┘
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│  2.  MIGRATION AGENT                                            🟢        │
│      discover schemas ─▶ sample rows ─▶ write+test pandas ─▶ classify    │
│      (information_schema)            (self-repair on error)   (mean ± 2σ) │
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
│      audits the mapping  →  verdict: APPROVE  or  FLAG                   │
└───────────────┬───────────────────────────────────┬──────────────────────┘
                │ FLAG                                │ APPROVE
                ▼                                     ▼
┌──────────────────────────────┐      ┌──────────────────────────────────┐
│  5a. FORCE ALL → review   ⚪ │      │  6.  SPLIT BY risk_level      ⚪  │
│      (auto-promote skipped)  │      │                                   │
└──────────────┬───────────────┘      │   auto ───────▶ AUTO-PROMOTE      │
               │                       │                 → production      │
               │                       │                                   │
               └───────────────────────┤   review ─────▶ HOLD in staging   │
                                        └──────────────┬────────────────────┘
                                                       ▼
┌────────────────────────────────────────────────────────────────────────┐
│  7.  HUMAN REVIEW  (only the flagged / uncertain rows)        👤        │
│      approve / exclude / reject     [+ Review Resolution Agent 🟢]       │
│      approved → production                                               │
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
│      recompute thresholds · reconcile staged = live + removed + pending  │
│      compare staging vs production values                                │
│                                                                          │
│      REPORT:  Overall PASS   or   NEEDS REVIEW                          │
└────────────────────────────────────────────────────────────────────────┘
```

Three properties the diagram makes explicit:
1. **The only AI that touches the data (box 2) is read-only.** Everything that *writes* (boxes 3,
   4, 6, 8) is deterministic code — that is the trust boundary.
2. **The plan flows down, not the AI.** Box 2 emits `promotion_config`; every box below executes it.
3. **Two safety gates + an independent auditor:** the Critic can divert everything to humans, humans
   handle only the uncertain rows, and a separate agent grades the result.

---

## 3. Component taxonomy

The word "agent" is used precisely. There are three categories:

### 🟢 Agentic — true tool-calling agents
An LLM in a loop that chooses which tools to call until a goal is met.

| Agent | File | Tools | Notes |
|---|---|---|---|
| **Migration Agent** | `app/agents/migration_agent.py` | 5 | Discovers schemas, samples data, writes & **self-repairs** a pandas transform, screens anomalies, emits `promotion_config` |
| **Review Resolution Agent** | `app/agents/review_agent.py` | 5 | NL message → approve/exclude wells; writes attributed audit events |
| **Verification Agent** | `app/agents/verification_agent.py` | 8 (read-only) | Mandatory-tool battery + discretionary investigation; independently re-checks the migration's classification |
| **Orchestrator Agent** *(opt-in)* | `app/agents/orchestrator_agent.py` | 8 | LLM-driven pipeline that reasons step-by-step and delegates to the sub-agents instead of running the fixed flow. Enabled with `ORCHESTRATOR_AGENT=true`; off by default (see §8) |

### 🟡 LLM-powered but not agentic — single shot
One LLM call, no loop, no tools.

| Component | File | Role |
|---|---|---|
| **Mapping Critic** | `app/agents/critic_agent.py` | Reviews `promotion_config` before promotion → `APPROVE`/`FLAG` (proposer–critic) |
| **Report (legacy)** | `app/agents/report_agent.py` | Deterministic fallback if the Verification Agent fails |

### ⚪ Deterministic code — no LLM
| Area | File(s) |
|---|---|
| Orchestration | `app/api/agent.py` (default fixed pipeline; delegates to the Orchestrator Agent when `ORCHESTRATOR_AGENT=true` — see §8) |
| Shared pipeline steps | `app/api/pipeline_helpers.py` (`write_to_staging`, `store_migration_plan`, `mark_source_migrating`, `force_all_to_review`, `purge_committed_sources`) — imported by both the fixed pipeline and the Orchestrator Agent to avoid circular imports |
| Source backup | `app/core/backup.py` (provider snapshot + poll), `app/core/backup_store.py` (SQLite metadata log) |
| Writes & promotion | `app/api/migration.py` (`_promote_rows`, approve/reject, upsert-key guard, restore-point lookup, audit log) |
| Read APIs | `app/api/abase.py`, `app/api/gds.py`, `app/api/report.py`, `app/api/reviewer.py`, `app/api/critic.py` |
| Plumbing | `app/connectors/*` (pools), `app/core/llm.py` (Gemini client + failover), `app/core/mapping.py` (validation, coercion, config loader) |

**Headline:** 3 always-on agents (+1 opt-in orchestrator) · 1 single-shot critic · 1 fallback · deterministic infrastructure.

---

## 4. Dual-database setup

- **Source (ABASE):** legacy system — `users`, `experiments`.
- **Target (GDS):**
  - `gds_users` — entities (researchers), unique on `name`
  - `gds_experiments` — production records, unique on `(gds_user_id, well_position)`, immutable once approved
  - `gds_staging_experiments` — transient staging buffer (`data` JSONB, `status`, `risk_level`)
  - `migration_plans` — stores each run's `promotion_config`
  - `migration_audit_log` — append-only audit trail
  - `migration_source_backups` — restore-point metadata per run (snapshot id, source host, row
    counts per table); read back via `GET /api/migrate/restore-point/{trace_id}`

Connection strings live in `backend/.env`. Passwords with special characters are percent-encoded
and decoded by the shared `_create_pool()`. Pools are async (`asyncpg`), managed by the FastAPI
lifespan.

---

## 4b. Pre-migration backup (restore point)

Before any change, `_execute_migration()` calls `trigger_backup()` to snapshot the **source** DB —
this is enterprise infrastructure, not agent logic, so the agent never reads or copies data rows; it
only invokes the configured provider and records the returned snapshot id.

- **Providers** (`BACKUP_PROVIDER` env var): `aws_rds` (RDS `CreateDBSnapshot` via boto3),
  `supabase` (Management API), `webhook` (generic HTTP, for enterprise/Oracle backup managers), or
  `none` (development only — logs a warning).
- **Non-blocking by design.** If backup is unavailable (`none`, or the table isn't created yet) the
  deterministic pipeline logs a warning and proceeds. The **Orchestrator Agent, by contrast, refuses
  to migrate without a confirmed backup** (see §8) — a stricter posture for production.
- **Metadata** is persisted both to `migration_source_backups` (Postgres) and a zero-setup local
  SQLite log at `backend/data/backup_log.db` (`backup_store.py`), keyed by `trace_id`.

---

## 5. The `promotion_config` contract

The Migration Agent emits this JSON; the promotion code consumes it. Example shape:

```json
{
  "staging_table": "gds_staging_experiments",
  "entity_table":  { "name": "gds_users", "column_map": {"scientist_name": "name", "scientist_role": "role"},
                     "upsert_key": "name", "pk": "gds_user_id" },
  "records_table": { "name": "gds_experiments", "column_map": {"well_position": "well_position", "signal": "signal"},
                     "fk_column": "gds_user_id", "upsert_keys": ["gds_user_id", "well_position"] },
  "anomaly_thresholds": { "signal": {"low": 1.97, "high": 18.47, "method": "mean_2sigma"} }
}
```

**Safety guards on the way in:**
- Every table/column identifier is validated (`validate_identifier`) before being used in SQL.
- The agent can pick upsert keys that don't match a real constraint; `_resolve_conflict_target`
  validates the proposed `ON CONFLICT` columns against the table's *actual* unique/PK constraints
  and falls back to the real one. **Agent proposes, infrastructure guarantees.**

> **Supabase note:** `_resolve_conflict_target` queries `pg_constraint` / `pg_class` / `pg_attribute`
> rather than `information_schema.key_column_usage`. The `information_schema` view returns empty rows
> on Supabase's pgBouncer pooler sessions (session-scoped visibility), which caused a silent fallback
> to unvalidated ON CONFLICT keys and a PostgreSQL `InvalidColumnReferenceError` on HITL approve.
> `pg_catalog` is always accessible and is the correct choice for DDL introspection on Supabase.

---

## 6. Anomaly screening (statistical, dynamic)

The Migration Agent computes thresholds from the actual sampled data — nothing hardcoded:

```
mean    = average of the measurement column
std_dev = standard deviation
flagged = value < mean − 2σ  OR  value > mean + 2σ
```

Flagged records get `risk_level = 'review'`; the rest `risk_level = 'auto'`. The math is executed
by numpy/pandas (deterministic), not by the LLM.

The **Verification Agent independently recomputes** these thresholds and compares its
classification to the Migration Agent's — one agent auditing another.

---

## 7. Human-in-the-loop (HITL)

- **Auto records** (`risk_level='auto'`) → promoted to production immediately, no human needed,
  unless the Mapping Critic flags the run (see Gate 1), in which case every record is forced to `review`.
- **Review records** (`risk_level='review'`) → held in staging; a human approves, excludes, or
  rejects them (directly, or via the Review Resolution Agent acting on a researcher's message).

Every resolution writes an attributed event (`hitl_approved` / `hitl_excluded` / `hitl_rejected`,
with the actor) to `migration_audit_log`, so the verification report can show who did what, when.

A second gate (**Gate 1 — mapping review**) surfaces the *draft mapping* for human review, backed by
the Mapping Critic's verdict. The escalation rule is **severity-based**: auto-promotion is skipped and
every record forced into HITL only when the critic returns `FLAG` **and** at least one finding is
`error`-severity (a type mismatch or data-corruption/promotion-failure risk). Warning- and info-level
findings are surfaced in the UI but **do not** block auto-promotion — clean rows still promote. A
critic *failure* (LLM unavailable) is non-blocking.

---

## 8. Orchestration — two interchangeable drivers

`_execute_migration()` in `app/api/agent.py` has two modes, selected by the `ORCHESTRATOR_AGENT`
env var:

- **Default — deterministic pipeline (`ORCHESTRATOR_AGENT=false`).** A fixed, linear sequence
  (backup → mapping → stage → critic → auto-promote → cleanup). For a known pipeline (ABASE → GDS)
  the order is fixed, so a hardcoded flow is correct, predictable, and cheap (no extra LLM turns).
- **Opt-in — Orchestrator Agent (`ORCHESTRATOR_AGENT=true`).** `app/agents/orchestrator_agent.py`
  is a true tool-calling agent (8 tools, up to 25 turns) that *reasons about each step* and decides
  what to do next rather than following a script. It enforces the same safety invariants explicitly:
  it **will not migrate without a confirmed backup** (`check_backup_status` must return
  `confirmed=true`, else it stops), escalates **all** rows to HITL when the critic finds
  error-severity issues, flags unusual runs (e.g. >50% of rows flagged), and can reason about
  retrying a failed sub-agent once. Both modes call the same sub-agents and the same shared
  `pipeline_helpers`, so they produce equivalent writes — the orchestrator simply makes the control
  flow itself an agent decision.

This is the realization of the former "v2 direction": the orchestrator earns its place when the
workflow needs real branching (unknown schemas, retry loops, early escalation), while the
deterministic path remains the safe, low-cost default.

---

## 9. Resilience

`app/core/llm.py` wraps every Gemini call with **model failover**: on a transient error (429/503/
overload) it retries on the next model in the chain (`gemini-2.5-flash` → `gemini-2.0-flash` →
`gemini-2.5-flash-lite`) before backing off (2s/4s/8s). A non-transient error on the requested
model is surfaced immediately rather than masked.

---

## 10. Traceability

Every run has a UUID `trace_id` that flows through: the agent run → staging rows → `migration_plans`
→ `migration_audit_log` → HITL decisions → the verification report. Use it to audit, inspect, or
roll back a single migration.

---

## 11. Tech stack

| Layer | Technology |
|---|---|
| Backend | Python 3.11+, FastAPI, asyncpg |
| AI | Google Gemini (`google-genai` SDK) |
| Data transform | pandas, numpy |
| Frontends | Next.js / React (×3) |
| Database | PostgreSQL (Supabase in the reference deployment); SQLite for the local backup log |
| Backup providers | AWS RDS (boto3), Supabase Management API, generic webhook |
