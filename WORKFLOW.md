# Workflow

The end-to-end migration pipeline, step by step. Each run is identified by a `trace_id`.

---

## Overview

```
  RUN MIGRATION            REVIEW (HITL)              VERIFY
  ─────────────            ───────────                ──────
  discover schemas         flagged records →          audit the run →
  map columns              approve / exclude / reject enterprise report
  screen anomalies               │                        │
  auto-promote clean             ▼                        ▼
  stage flagged            attributed audit events   Overall: PASS / NEEDS REVIEW
```

---

## Stage 1 — Migration  (`POST /api/agent/run`)

Optional body: `source_db_url`, `target_db_url`, `initiated_by`. Defaults to ABASE → GDS.

> **Two drivers.** By default this stage runs as a fixed deterministic pipeline. Set
> `ORCHESTRATOR_AGENT=true` to instead drive it with the **Orchestrator Agent** — a tool-calling
> agent that reasons step-by-step, refuses to migrate without a confirmed backup, and escalates to
> HITL on its own judgment. Both produce equivalent writes; the steps below describe the default path.

0. **Back up the source (non-blocking).** Before any change, `trigger_backup()` snapshots the source
   DB via the configured provider (`BACKUP_PROVIDER`: `aws_rds` / `supabase` / `webhook` / `none`).
   The snapshot id and per-table row counts are recorded to `migration_source_backups` and a local
   SQLite log. If backup is unavailable the deterministic pipeline logs a warning and proceeds (the
   Orchestrator Agent stops instead). Inspect later via `GET /api/migrate/restore-point/{trace_id}`.
1. **Run the Migration Agent (read-only).** It:
   - discovers the source and target schemas via `information_schema`
   - samples real rows to understand each column's meaning
   - writes a pandas+scipy transform (self-repairing on error) that:
     - groups wells by (scientist, compound) into one dose-response series
     - normalizes raw RFU → % inhibition using per-plate neg/pos control wells
     - fits the Hill equation (scipy `curve_fit`) to compute EC50, Hill slope, Emax, R²
     - classifies each compound: R² ≥ 0.90 → `auto`; R² < 0.90 → `review`
   - emits `cleaned_records` (one row per compound) + a `promotion_config` JSON mapping
2. **Write to staging.** Infrastructure writes all records to `gds_staging_experiments` as JSONB
   (`status='pending'`).
3. **Mark the source `migrating`.** Involved ABASE researchers are set to
   `migration_status='migrating'`. No source data is deleted here.
4. **Store the plan.** `promotion_config` is saved to `migration_plans` for this `trace_id`.
5. **Mapping Critic.** A single-shot LLM audit of the mapping → `APPROVE` / `FLAG` with per-finding
   severity. A critic *failure* (LLM unavailable) does not block the run. Escalation is
   **severity-based**: every staged row is flipped to `risk_level='review'` and auto-promotion
   skipped **only** when the verdict is `FLAG` *and* there is at least one `error`-severity finding.
   Warning/info findings are surfaced for the human but do **not** block auto-promotion.
6. **Auto-promote clean records.** If the critic did not flag, records with `risk_level='auto'` are
   promoted to `gds_experiments` immediately — no human needed.
7. **Delete fully-migrated sources.** After promotion commits, a researcher is hard-deleted from
   ABASE only if every one of their staged rows is `auto_approved`. Anyone with a flagged or pending
   row stays in ABASE (`migration_status='migrating'`) until HITL resolves them.

**Result:** `N` staged, `X` auto-promoted, `Y` pending review. If `Y > 0`, HITL is required.

---

## Stage 2 — Human review (HITL)

Flagged records are resolved in one of two ways. Either path writes an **attributed** audit event
(`hitl_approved` / `hitl_excluded` / `hitl_rejected`, with the actor and timestamp).

**A. Directly in the HITL Console** (`http://localhost:3000/review?trace_id=…`)
- Approve → promoted to production
- Reject (`POST /api/migrate/reject/{trace_id}`) → not migrated

**B. Via the Review Resolution Agent** (`http://localhost:3000/reviewer?trace_id=…&scientist=…`)
- An admin types what the researcher communicated in plain English
- The agent interprets it and calls `approve_well` / `exclude_well` / `approve_all_wells` /
  `exclude_all_wells` as appropriate

Each record ends in exactly one state: **accepted** (live), **removed** (excluded or rejected), or
**pending**.

---

## Stage 3 — Verification report  (`POST /api/report/{trace_id}`)

The **Verification Agent** audits the completed run. It runs a mandatory battery of read-only tools
and investigates anything anomalous:

| Mandatory tool | Confirms |
|---|---|
| `get_migration_summary` | counts, field mapping, completeness |
| `check_reconciliation` | staged = live + removed + pending; researcher balance |
| `recompute_anomaly_threshold` | independent re-check of the migration's R²-based classification |
| `compare_staging_vs_production` | migrated values match source exactly |
| `get_per_scientist_breakdown` | per-researcher accepted / removed / pending |
| `get_audit_timeline` | who did what, when (incl. manual approvals/rejections) |

It then writes a business report with sections: Executive Summary, Migration Scope, Field Mapping,
Data Quality & Validation, Exceptions & Resolutions, Reconciliation, Accountability & Audit Trail,
and an **Overall: PASS / NEEDS REVIEW** sign-off.

> All numbers come from deterministic tools; the agent decides what to investigate and writes the
> narrative. It never computes a figure itself and never writes to the database.

If the agentic verifier fails (e.g. Gemini unavailable), the deterministic `report_agent` fallback
produces an equivalent report.

---

## Other operations

| Action | Endpoint |
|---|---|
| List completed runs | `GET /api/report/completed` |
| Inspect pending review rows | `GET /api/migration/review?trace_id=…` |
| Inspect the run's restore point | `GET /api/migrate/restore-point/{trace_id}` |
| Roll back an approved run | `POST /api/migrate/rollback/{trace_id}` |
| Re-run the mapping critic | `POST /api/critic/{trace_id}` |
| Reset demo data | `python backend/scripts/reset_demo.py` |

---

## State & status reference

- **Source `migration_status` (ABASE `users`):** `active` → `migrating` (records staged) → row
  hard-deleted after its records are confirmed live in GDS.
- **Staging `risk_level`:** `auto` (clean) · `review` (flagged, or forced by a critic `FLAG`)
- **Staging `status`:** `pending` → `auto_approved` / `approved` / `excluded` / `rejected`
- **Audit events:** `auto_approved`, `hitl_approved`, `hitl_excluded`, `hitl_rejected`
- **Report verdict:** `Overall: PASS` (everything reconciles, accurate, nothing pending) else
  `Overall: NEEDS REVIEW`
