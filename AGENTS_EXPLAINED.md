# Agent Code Explained — Plain English Guide

Every agent file lives in `backend/app/agents/`. This document walks through each one:
what it does, how it works, what its tools do, and why it was built the way it was.

---

## How Agents Work (The Common Pattern)

Before reading individual files, understand the shared pattern all agentic files follow.

**The tool-calling loop:**

```
You → send a message to Gemini
Gemini → decides which tool to call
You → execute the tool, send the result back
Gemini → reads the result, decides the next tool
... repeat until Gemini stops calling tools and writes a final answer
```

Every agent file contains:
1. **Tool functions** (`_tool_*`) — Python functions that actually do the work
2. **Tool definitions** (`TOOL_DEFINITIONS`) — descriptions of those functions in Gemini's format, so the LLM knows they exist and what parameters they take
3. **A system prompt** (`SYSTEM_PROMPT`) — instructions that tell the LLM what it is, what its job is, and what rules to follow
4. **An agent loop** (`run_*_agent()`) — the `for turn in range(MAX_TURNS)` loop that sends messages to Gemini, dispatches tool calls, and collects the final answer

**The one invariant that never breaks:**
> Agents only call read-only or in-memory tool functions. Every database write happens in `app/api/` code, never inside an agent.

---

## File 1: `migration_agent.py`
**The core agent. Does the actual data migration work.**

### What it does
Takes two database connection strings (source and target), figures out what data needs to move, how to transform it, and returns the cleaned records plus a configuration file describing how to write them to the target.

It never writes to any database. It hands everything back to `api/agent.py` which does the actual writing.

### Type
**Agentic** — true tool-calling loop, up to 15 turns, with self-repair on errors.

### The 5 Tools

| Tool | What it does |
|---|---|
| `discover_source_schema` | Reads `information_schema` on the source DB — gets table names, column names, data types, foreign keys, row counts |
| `discover_target_schema` | Same for the target DB — agent identifies the staging buffer table vs the production tables |
| `sample_source_data` | Fetches up to 20 real rows from any source table so the agent can see actual data values, not just column names |
| `write_and_test_mapping` | The agent writes a pandas Python script. This tool executes it in-memory (no DB). If it fails, the agent reads the error and rewrites it. |
| `store_promotion_config` | Agent stores a JSON config (how its intermediate columns map to the real target tables) in run state. No DB write — just memory. |

### The Self-Repair Loop
When `write_and_test_mapping` fails, it returns `{"status": "error", "error": "...traceback..."}`. The agent reads that error, understands what went wrong, and calls `write_and_test_mapping` again with a corrected script. This can repeat up to `MAX_TOOL_FAILURES = 5` times before giving up.

### Dose-Response Pipeline
When the source has a concentration column, the system prompt tells the agent to:
1. Group rows by (scientist, compound) — many wells → one curve
2. Separate DMSO and reference inhibitor control wells from sample wells
3. Normalize raw RFU → % inhibition using `(neg_ctrl_mean - raw) / (neg_ctrl_mean - pos_ctrl_mean) × 100`
4. Fit the Hill equation using `curve_fit` from scipy: `y = Emax / (1 + (EC50 / c)^n)`
5. Compute R², classify each compound, set `risk_level='review'` if R² < 0.90
6. Output one row per (scientist, compound) with EC50, Hill slope, R², Emax, curve_data

The `curve_fit` function is injected into the pandas script's namespace so the agent can call it directly.

### The Early-Stop Guard
If Gemini tries to stop before calling both `write_and_test_mapping` AND `store_promotion_config`, the loop injects a message: *"You stopped early. You must still call: [missing tools]."* This forces the agent to finish its mandatory workflow even if it thinks it's done.

### Key State — `_RunState`
A dataclass that holds:
- `source_pool` / `target_pool` — database connections
- `source_tables` — all source data loaded as pandas DataFrames (READ ONCE, reused)
- `cleaned_df` — the transformed result DataFrame
- `promotion_config` — the JSON mapping the agent produced
- `transform_script` — the pandas script that was used (saved so it can be replayed)

Per-run, no module-level globals — multiple runs can happen simultaneously safely.

### Schema Fingerprinting + Cache
`compute_schema_fingerprints()` hashes the column structure of both databases. If the same schema pair has been migrated before, the saved `transform_script` is replayed deterministically (no LLM call needed). This is the `migration_mappings` table.

### What it returns
```python
{
    "trace_id":         "...",
    "cleaned_records":  [...],        # list of dicts, one per compound
    "promotion_config": {...},        # how to write cleaned_records to target tables
    "transform_script": "...",        # the pandas script (for caching)
    "quality_verdict":  "WARN/PASS",
    "reasoning_trace":  [...],        # every tool call + result, for debugging
    "turns_used":       8,
}
```

---

## File 2: `orchestrator_agent.py`
**The manager agent. Coordinates all other agents and tools.**

### What it does
Instead of a fixed pipeline (backup → ETL → stage → critic → promote), the Orchestrator is an LLM that *reasons* about each step. It decides what to do next based on what it finds. Enable with `ORCHESTRATOR_AGENT=true` in `.env`.

### Type
**Agentic** — tool-calling loop, up to 25 turns, coordinates sub-agents.

### The 8 Tools

| Tool | What it calls underneath |
|---|---|
| `trigger_backup` | `app/core/backup.py → trigger_backup()` — snapshots the source DB |
| `check_backup_status` | `app/core/backup.py → check_backup_status()` — polls until confirmed |
| `run_sme_etl_agent` | `migration_agent.run_migration_agent()` — runs the full Migration Agent |
| `write_to_staging` | `pipeline_helpers.write_to_staging()` — writes cleaned records to DB |
| `run_qa_critic` | `critic_agent.run_critic_agent()` — runs the Mapping Critic |
| `auto_approve_clean_rows` | `migration.auto_approve_clean_rows()` — promotes R²-good rows |
| `escalate_all_to_review` | `pipeline_helpers.force_all_to_review()` — flips all rows to HITL |
| `get_pipeline_status` | SQL query — reads current staging counts by status |

### The Key Rules Enforced
The system prompt contains hard rules the Orchestrator must follow:
- **Never migrate without a confirmed backup.** If `check_backup_status` returns `confirmed=false`, stop immediately.
- **Critic error → escalate all rows.** If `run_qa_critic` returns `has_errors=true`, call `escalate_all_to_review`, never `auto_approve_clean_rows`.
- **Critic warn/approve → auto-promote clean rows.** Warnings don't block anything.
- **>75% review rows → flag it but still auto-promote the clean ones.** Don't halt, just note it.

### The `_State` Dataclass
Holds everything accumulated across turns: backup result, agent result, cleaned records, staging count, critic result, auto-promoted count, pending review count. All tools read from and write to this shared state.

### When to use it vs the deterministic pipeline
The deterministic pipeline (`ORCHESTRATOR_AGENT=false`) always runs the same fixed sequence. The Orchestrator can retry a failed sub-agent, reason about unusual situations, and adapt. Use the Orchestrator when you want agentic judgment; use the deterministic pipeline for predictability and lower cost.

---

## File 3: `critic_agent.py`
**The auditor. One LLM call that reviews the column mapping before any data moves.**

### What it does
After the Migration Agent produces a `promotion_config` (the column mapping), the Critic reads it alongside the actual target table schema and real sample data, and produces a verdict: `APPROVE` or `FLAG`.

### Type
**Single-shot** — one LLM call, no tool loop, no tools.

### How it gathers data (`_gather_critic_data`)
Before calling Gemini, it fetches from the DB:
- The `promotion_config` from `migration_plans` for this `trace_id`
- The real target table schema from `information_schema`
- Which columns have DB defaults (auto-filled by the DB, never from source data)
- 5 sample staged rows so the LLM can see real values

It also strips out infrastructure-managed columns from the config before showing it to the LLM (the FK column, `trace_id`, `approved_by`) — these are provably handled by the promotion code, not by source data, so showing them to the critic would cause false-positive errors.

### What the Critic checks
1. **Type mismatch** — is a text value being written into a numeric column?
2. **Semantic mismatch** — is an ID column mapped to a measurement column?
3. **Missing mapping** — a target column has an obvious source equivalent but wasn't mapped
4. **Unit/scale risk** — concentration_um vs concentration — might there be a silent unit change?
5. **Upsert key sanity** — are the ON CONFLICT keys valid? (Always `info` severity, never blocking — the infra auto-corrects these)

### Severity levels
- `error` — will corrupt data or break promotion → causes `FLAG` and forces all rows to HITL
- `warning` — likely wrong, human should confirm → does NOT block auto-promotion
- `info` — minor note → does NOT block auto-promotion

### The Deterministic Guard (important)
After the LLM responds, the code runs a deterministic post-processing step that downgrades any `error`/`warning` finding that mentions:
- An auto-filled column (infrastructure-managed — promotion code ignores it anyway)
- An upsert key issue (the infra auto-corrects these against real pg_constraint)
- A text-typed target column (any source value casts losslessly into text)

This prevents LLM false positives from forcing every row to human review unnecessarily. The verdict is then recomputed from the (possibly downgraded) findings.

### What it returns
```python
{
    "verdict":    "APPROVE",         # or "FLAG"
    "confidence": "high",
    "summary":    "Mapping looks correct.",
    "findings":   [
        {"severity": "info", "field": "scientist_name -> name",
         "issue": "...", "recommendation": "..."}
    ],
}
```

---

## File 4: `review_agent.py`
**The human-interface agent. Turns plain English into approve/exclude decisions.**

### What it does
An admin types what a scientist communicated (e.g. *"she confirmed B03 was a calibration error but everything else is fine"*). The agent reads the pending staging rows for that scientist, interprets the message, and calls approve/exclude tools accordingly.

### Type
**Agentic** — tool-calling loop, up to 12 turns. Has two modes: per-scientist and batch.

### The 5 Tools (per-scientist mode)

| Tool | What it does |
|---|---|
| `get_scientist_wells` | Reads all staging rows for one scientist — shows staging_ids, well positions, signals, statuses |
| `approve_well` | Promotes a single staging row to `gds_experiments` (calls `_promote_single_row`) |
| `exclude_well` | Sets a single staging row to `status='excluded'` |
| `approve_all_wells` | Promotes all pending rows for this scientist |
| `exclude_all_wells` | Excludes all pending rows for this scientist |

### The `_promote_single_row` Function
This is where the actual DB write happens inside the Review Agent's tools. It:
1. Reads the staging row's JSONB data
2. Upserts the entity (e.g. `gds_users`) using the `entity_table` config
3. Inserts the record (e.g. `gds_experiments`) using the `records_table` config
4. Updates the staging row status to `approved`
5. All in a single DB transaction

Every column name is validated with `validate_identifier()` before use in SQL (SQL injection prevention).

### Intent Categories the Agent Recognizes
The system prompt teaches the agent to map messages into one of these intents:
- **Approve specific wells** — "everything except B03 is fine"
- **Approve all** — "she confirmed everything is correct"
- **Exclude specific wells** — "B03 and B04 had calibration errors"
- **Exclude all** — "equipment failure — all readings are unreliable"
- **Ambiguous** — only when there are multiple pending wells and the message doesn't specify which ones

### The 1-Pending-Well Rule
If there is exactly 1 pending well, the agent never asks for clarification. Any message like "invalid reading", "she said it's fine", "equipment error" — the agent knows the message refers to the only pending well and acts immediately.

### Audit Logging
Every action calls `_log_review_audit()` which writes to `migration_audit_log` with the event type (`hitl_approved` / `hitl_excluded`), actor, promoted/excluded count, and timestamp. This is what appears in the verification report's audit trail.

### Batch Mode (`run_batch_review_agent`)
A second mode that acts across ALL scientists at once. The admin types one instruction like *"remove Chen_L's data and approve everyone else"* and the agent handles all of them. Uses a separate `BATCH_SYSTEM_PROMPT` and `BATCH_TOOL_DEFINITIONS` with a `get_all_pending_wells` tool.

---

## File 5: `verification_agent.py`
**The auditor. Independently checks that the migration was done correctly.**

### What it does
After a migration is complete, this agent runs an independent audit. It re-checks the Migration Agent's work, reconciles counts, verifies data integrity, and produces a business-grade compliance report ending in `Overall: PASS` or `Overall: NEEDS REVIEW`.

### Type
**Agentic** — tool-calling loop, up to 15 turns. Has mandatory tools (always run) and discretionary tools (run when it finds something suspicious).

### Mandatory vs Discretionary Tools

**Mandatory (must all be called before the agent can write its report):**

| Tool | What it checks |
|---|---|
| `get_migration_summary` | Total counts (staged, auto-approved, HITL-approved, excluded, pending, in production), field mapping used, null analysis across all JSONB rows |
| `check_reconciliation` | Verifies: staged = promoted + excluded + pending (row balance). Every scientist with approved rows is in GDS (scientist balance). |
| `recompute_anomaly_threshold` | Independently recomputes R² thresholds from staging data and compares to the Migration Agent's classification. Finds any mismatches. |
| `compare_staging_vs_production` | Spot-checks that values in staging match values in production (signal integrity — no corruption during promotion) |
| `get_per_scientist_breakdown` | Per-scientist accepted/removed/pending counts + the specific removed records |
| `get_audit_timeline` | Ordered list of every audit event (who did what, when) |

**Discretionary (called when something looks wrong):**

| Tool | Used for |
|---|---|
| `query_rows` | Pull specific staging rows to investigate a discrepancy |
| `trace_to_source` | Check that production rows have `source_experiment_id` pointing back to ABASE |

### The Mandatory Tool Guard
The loop has an early-stop guard: if the agent tries to write its final report before calling all 6 mandatory tools, the loop injects a message: *"You have not finished the mandatory audit. Still required: [list]. Call them, then write the report."* The agent cannot finish early.

### The AI Never Computes Numbers
Every figure in the report — every count, every balance check, every percentage — comes from a tool. The agent decides which tools to call and writes the narrative around the numbers. It never calculates anything in its own reasoning.

### Report Audience
The system prompt explicitly says: **write for a non-technical executive**. This means:
- No statistics jargon — never write "mean", "2σ", "threshold", "staging"
- Translate: "flagged readings" instead of "anomaly detection"; "values match the original source exactly" instead of "signal integrity spot-check passed"
- Every number must come from a tool — no rounding, no inventing

### What it returns
```python
{
    "trace_id":    "...",
    "report":      "DATA MIGRATION REPORT\n══════...\nOverall: PASS",
    "data":        {...},      # all the raw evidence gathered by tools
    "generated_at": "...",
    "reasoning_trace": [...],  # every tool call + result
    "turns_used":  12,
}
```

---

## File 6: `report_agent.py`
**The fallback. Used when the Verification Agent fails.**

### What it does
Same job as the Verification Agent — produce a verification report — but done differently. Instead of an agentic loop, it:
1. Gathers all evidence in a single Python function (`_gather_report_data`) — no LLM involved
2. Passes the full evidence blob to Gemini in one call
3. Gemini writes the report from the pre-gathered data

### Type
**Single-shot** — one LLM call. Not agentic. No tools.

### When it runs
Only when the Verification Agent throws an unhandled exception (e.g. Gemini completely unavailable, connection error). `api/report.py` catches the exception and falls back to this file.

### Why it exists
Fail-open: a broken verification agent should not prevent you from getting a report. The report from this fallback uses a more structured template (less narrative, more checklist) but contains the same data.

### How it differs from the Verification Agent

| Aspect | Verification Agent | Report Fallback |
|---|---|---|
| Type | Agentic loop | Single-shot |
| Investigation | Agent investigates anomalies | Fixed data gathering, no investigation |
| LLM calls | Up to 15 turns | Exactly 1 |
| Can dig deeper | Yes — if something looks wrong, it calls more tools | No — fixed data set |
| Audience | Executive narrative | Compliance checklist format |

---

## How All 6 Files Fit Together

```
POST /api/agent/run
        │
        ▼
orchestrator_agent.py   ← (if ORCHESTRATOR_AGENT=true)
        │   OR
api/agent.py            ← (deterministic pipeline, default)
        │
        ├─▶  migration_agent.py     ← discovers schema, fits Hill curves, transforms data
        │
        ├─▶  critic_agent.py        ← audits the column mapping BEFORE any write
        │
        ├─▶  api/migration.py       ← does the actual DB writes (not an agent file)
        │
        ├─▶  review_agent.py        ← human review loop (called later by /reviewer endpoint)
        │
        └─▶  verification_agent.py  ← final audit (called later by /report endpoint)
                    │
                    └─▶  report_agent.py  ← fallback if verification_agent fails
```

---

## Key Shared Patterns

### `generate_with_backoff(client, contents, config, model)`
Every agent uses this instead of calling the Gemini API directly. It handles:
- Model failover: `gemini-2.5-flash` → `gemini-2.0-flash` → `gemini-2.5-flash-lite`
- Retry with backoff on 429/503 errors
- Immediate surface on non-transient errors

### `validate_identifier(name, context)`
Used in `review_agent.py` and `migration.py` before every agent-supplied string goes into a SQL query. Prevents SQL injection from LLM-generated table/column names.

### `temperature=0.1`
All agents use low temperature. This is deliberate — you want the LLM to be consistent and precise, not creative. The creativity is in the reasoning; the output must be reliable.

### Logging + `reasoning_trace`
Every agent returns a `reasoning_trace` list — every tool call and result, in order, with timestamps. This appears in the backend log (`backend/logs/backend.log`) and is available in the API response. Use it to debug why an agent made a particular decision.
