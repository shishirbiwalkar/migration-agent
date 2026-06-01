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

## 2. Component taxonomy

The word "agent" is used precisely. There are three categories:

### 🟢 Agentic — true tool-calling agents
An LLM in a loop that chooses which tools to call until a goal is met.

| Agent | File | Tools | Notes |
|---|---|---|---|
| **Migration Agent** | `app/agents/migration_agent.py` | 5 | Discovers schemas, samples data, writes & **self-repairs** a pandas transform, screens anomalies, emits `promotion_config` |
| **Review Resolution Agent** | `app/agents/review_agent.py` | 5 | NL message → approve/exclude wells; writes attributed audit events |
| **Verification Agent** | `app/agents/verification_agent.py` | 8 (read-only) | Mandatory-tool battery + discretionary investigation; independently re-checks the migration's classification |

### 🟡 LLM-powered but not agentic — single shot
One LLM call, no loop, no tools.

| Component | File | Role |
|---|---|---|
| **Mapping Critic** | `app/agents/critic_agent.py` | Reviews `promotion_config` before promotion → `APPROVE`/`FLAG` (proposer–critic) |
| **Report (legacy)** | `app/agents/report_agent.py` | Deterministic fallback if the Verification Agent fails |

### ⚪ Deterministic code — no LLM
| Area | File(s) |
|---|---|
| Orchestration | `app/api/agent.py` (fixed pipeline; not an orchestrator agent — see §7) |
| Writes & promotion | `app/api/migration.py` (`_promote_rows`, approve/reject, upsert-key guard, audit log) |
| Read APIs | `app/api/abase.py`, `app/api/gds.py`, `app/api/report.py`, `app/api/reviewer.py`, `app/api/critic.py` |
| Plumbing | `app/connectors/*` (pools), `app/core/llm.py` (Gemini client + failover), `app/core/mapping.py` (validation, coercion, config loader) |

**Headline:** 3 true agents · 1 single-shot critic · 1 fallback · deterministic infrastructure.

---

## 3. Dual-database setup

- **Source (ABASE):** legacy system — `users`, `experiments`.
- **Target (GDS):**
  - `gds_users` — entities (researchers), unique on `name`
  - `gds_experiments` — production records, unique on `(gds_user_id, well_position)`, immutable once approved
  - `gds_staging_experiments` — transient staging buffer (`data` JSONB, `status`, `risk_level`)
  - `migration_plans` — stores each run's `promotion_config`
  - `migration_audit_log` — append-only audit trail

Connection strings live in `backend/.env`. Passwords with special characters are percent-encoded
and decoded by the shared `_create_pool()`. Pools are async (`asyncpg`), managed by the FastAPI
lifespan.

---

## 4. The `promotion_config` contract

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

---

## 5. Anomaly screening (statistical, dynamic)

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

## 6. Human-in-the-loop (HITL)

- **Auto records** (`risk_level='auto'`) → promoted to production immediately, no human needed.
- **Review records** (`risk_level='review'`) → held in staging; a human approves, excludes, or
  rejects them (directly, or via the Review Resolution Agent acting on a researcher's message).

Every resolution writes an attributed event (`hitl_approved` / `hitl_excluded` / `hitl_rejected`,
with the actor) to `migration_audit_log`, so the verification report can show who did what, when.

A second, deferred gate (**Gate 1 — mapping review**) surfaces the *draft mapping* to a human
before any data moves; it is intended for unknown source schemas. Trusted pipelines like ABASE → GDS
proceed directly. The Mapping Critic already produces the verdict that backs this gate.

---

## 7. Orchestration (and why it isn't an agent — yet)

The end-to-end flow in `app/api/agent.py` is a **deterministic, linear pipeline**, not an
orchestrator agent. This is intentional: for a known pipeline (ABASE → GDS) the sequence is fixed,
so a hardcoded flow is correct and predictable. An orchestrator agent earns its place only when the
workflow needs real branching — unknown schemas, or a critic-rejects-then-remap loop. That is the
documented v2 direction.

---

## 8. Resilience

`app/core/llm.py` wraps every Gemini call with **model failover**: on a transient error (429/503/
overload) it retries on the next model in the chain (`gemini-2.5-flash` → `gemini-2.0-flash` →
`gemini-2.5-flash-lite`) before backing off (2s/4s/8s). A non-transient error on the requested
model is surfaced immediately rather than masked.

---

## 9. Traceability

Every run has a UUID `trace_id` that flows through: the agent run → staging rows → `migration_plans`
→ `migration_audit_log` → HITL decisions → the verification report. Use it to audit, inspect, or
roll back a single migration.

---

## 10. Tech stack

| Layer | Technology |
|---|---|
| Backend | Python 3.11+, FastAPI, asyncpg |
| AI | Google Gemini (`google-genai` SDK) |
| Data transform | pandas, numpy |
| Frontends | Next.js / React (×3) |
| Database | PostgreSQL (Supabase in the reference deployment) |
