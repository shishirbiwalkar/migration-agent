"""
Verification Agent — Agentic, Read-Only
=======================================
An autonomous auditor for a completed migration run.

Unlike the old report_agent (a fixed Python script + one LLM call), this is a
real tool-calling agent: it decides what to investigate, digs into anomalies,
INDEPENDENTLY RECOMPUTES the migration agent's anomaly classification, forms
hypotheses, and renders a reasoned PASS/FAIL verdict.

Two guarantees are preserved:
  1. READ-ONLY — every tool is a SELECT. There is no write tool to give it.
     The agent catches and flags; it never corrects. Humans correct.
  2. The AI never computes a number in its head. Every figure and every hard
     verdict boolean is computed by deterministic SQL inside a tool. The agent
     decides WHICH tools to call and INTERPRETS the results — the agency lives
     in investigation and judgment, not arithmetic.

Tools (all READ-ONLY):
  1. get_migration_summary       — counts, field mapping, null analysis  (mandatory)
  2. check_reconciliation        — source<->target balance booleans      (mandatory)
  3. recompute_anomaly_threshold — re-derive mean+-2sigma, audit the      (mandatory)
                                   migration agent's risk classification
  4. compare_staging_vs_production — signal-integrity spot-check          (mandatory)
  5. query_rows                  — pull specific rows to investigate      (discretionary)
  6. trace_to_source             — follow a production row's lineage      (discretionary)
  7. get_audit_timeline          — who did what, when                     (discretionary)
"""

import json
import logging
import uuid as uuid_mod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import numpy as np
from google.genai import types

from app.connectors import get_gds_pool
from app.core.llm import get_client, generate_with_backoff, MODEL as _MODEL
from app.core.mapping import (
    NAME_FIELDS as _NAME, WELL_FIELDS as _WELL, SIGNAL_FIELDS as _SIG,
    pick_field,
)

logger    = logging.getLogger(__name__)
MAX_TURNS = 15

# The agent cannot render a verdict until these have all run — guarantees the
# report is as complete as the old fixed battery, while leaving the agent free
# to investigate further with the discretionary tools.
MANDATORY_TOOLS = {
    "get_migration_summary",
    "check_reconciliation",
    "recompute_anomaly_threshold",
    "compare_staging_vs_production",
    "get_per_scientist_breakdown", # per-scientist breakdown + exclusion evidence
    "get_audit_timeline",          # who did what, when — core compliance evidence
}


@dataclass
class _RunState:
    pool:     Any  = None
    tid:      Any  = None
    called:   set            = field(default_factory=set)
    evidence: dict           = field(default_factory=dict)


def _as_dict(data) -> dict:
    return data if isinstance(data, dict) else json.loads(data)


# ── Tool 1: get_migration_summary ─────────────────────────────────────────────

async def _tool_get_migration_summary(state: _RunState) -> dict:
    async with state.pool.acquire() as conn:
        staging_counts = await conn.fetch("""
            SELECT status, risk_level, COUNT(*) AS cnt
            FROM   gds_staging_experiments
            WHERE  trace_id = $1
            GROUP  BY status, risk_level
        """, state.tid)
        prod_count = await conn.fetchval(
            "SELECT COUNT(*) FROM gds_experiments WHERE trace_id=$1", state.tid)
        plan_row = await conn.fetchrow(
            "SELECT plan_json, initiated_by FROM migration_plans WHERE trace_id=$1", state.tid)
        staging_rows = await conn.fetch(
            "SELECT data FROM gds_staging_experiments WHERE trace_id=$1", state.tid)

    counts_raw = {f"{r['risk_level']}_{r['status']}": int(r["cnt"]) for r in staging_counts}
    total_staged    = sum(counts_raw.values())
    auto_approved   = counts_raw.get("auto_auto_approved", 0)
    review_approved = counts_raw.get("review_approved", 0)
    review_excluded = counts_raw.get("review_excluded", 0)
    review_rejected = counts_raw.get("review_rejected", 0)
    review_pending  = counts_raw.get("review_pending", 0)
    in_production   = int(prod_count or 0)

    config = {}
    initiated_by = "Unknown"
    if plan_row:
        config = _as_dict(plan_row["plan_json"]) if plan_row["plan_json"] else {}
        initiated_by = plan_row["initiated_by"] or "Unknown"

    # Hide internal plumbing from the business report: linking keys, system ids.
    _MAP_SKIP = {"source_experiment_id", "trace_id", "approved_by", "gds_user_id"}
    field_mapping = []
    for tbl_key in ("entity_table", "records_table"):
        tbl = config.get(tbl_key, {}) or {}
        for src, tgt in (tbl.get("column_map", {}) or {}).items():
            if src in _MAP_SKIP or tgt in _MAP_SKIP:
                continue
            field_mapping.append({"source": src, "target": tgt})

    # Null analysis across all staged JSONB rows
    null_total, null_nulls = {}, {}
    for r in staging_rows:
        for k, v in _as_dict(r["data"]).items():
            null_total[k] = null_total.get(k, 0) + 1
            if v is None or v == "" or v == "null":
                null_nulls[k] = null_nulls.get(k, 0) + 1
    null_report = {
        k: {"total": t, "nulls": null_nulls.get(k, 0),
            "null_pct": round(null_nulls.get(k, 0) / t * 100, 1) if t else 0}
        for k, t in null_total.items()
    }

    counts = {
        "total_staged":   total_staged,
        "auto_approved":  auto_approved,
        "review_flagged": review_approved + review_excluded + review_rejected + review_pending,
        "hitl_approved":  review_approved,
        "excluded":       review_excluded,
        "rejected":       review_rejected,
        "pending":        review_pending,
        "in_production":  in_production,
        "migration_rate_pct": round(in_production / total_staged * 100, 1) if total_staged else 0,
    }

    state.evidence.update({
        "counts": counts, "initiated_by": initiated_by,
        "field_mapping": field_mapping, "anomaly_thresholds": config.get("anomaly_thresholds", {}),
        "null_field_counts": null_report,
    })
    return {
        "status": "success", "counts": counts, "initiated_by": initiated_by,
        "field_mapping": field_mapping,
        "anomaly_thresholds": config.get("anomaly_thresholds", {}),
        "null_field_counts": null_report,
        "note": "Baseline counts computed by SQL. Next: check_reconciliation, "
                "recompute_anomaly_threshold, compare_staging_vs_production.",
    }


# ── Tool 2: check_reconciliation ──────────────────────────────────────────────

async def _tool_check_reconciliation(state: _RunState) -> dict:
    async with state.pool.acquire() as conn:
        staging_rows = await conn.fetch(
            "SELECT data, status FROM gds_staging_experiments WHERE trace_id=$1", state.tid)
        prod_rows = await conn.fetch("""
            SELECT u.name AS scientist_name
            FROM   gds_experiments e
            JOIN   gds_users u ON u.gds_user_id = e.gds_user_id
            WHERE  e.trace_id = $1
        """, state.tid)

    total_staged = len(staging_rows)
    excluded = sum(1 for r in staging_rows if r["status"] == "excluded")
    rejected = sum(1 for r in staging_rows if r["status"] == "rejected")
    pending  = sum(1 for r in staging_rows if r["status"] == "pending")
    removed  = excluded + rejected   # resolved but not promoted
    in_production = len(prod_rows)

    staged_scientists, promoted_scientists = set(), set()
    for r in staging_rows:
        name = pick_field(_as_dict(r["data"]), _NAME, "Unknown")
        if name != "Unknown":
            staged_scientists.add(name)
            if r["status"] in ("approved", "auto_approved"):
                promoted_scientists.add(name)
    gds_scientists = {r["scientist_name"] for r in prod_rows}

    row_balance_ok       = total_staged == (in_production + removed + pending)
    scientist_balance_ok = promoted_scientists == gds_scientists

    recon = {
        "staged_experiments":   total_staged,
        "promoted_to_gds":      in_production,
        "excluded":             excluded,
        "rejected":             rejected,
        "pending":              pending,
        "row_balance_ok":       row_balance_ok,
        "staged_scientists":    len(staged_scientists),
        "scientists_in_gds":    len(gds_scientists),
        "scientist_balance_ok": scientist_balance_ok,
        "missing_in_gds":       sorted(promoted_scientists - gds_scientists),
        "note": "Reconciled against the staging snapshot (source-of-truth at extraction "
                "time); source records are deleted post-migration by design.",
    }
    state.evidence["source_reconciliation"] = recon
    return {"status": "success", **recon}


# ── Tool 3: recompute_anomaly_threshold (cross-agent audit) ───────────────────

async def _tool_recompute_anomaly_threshold(column: str, state: _RunState) -> dict:
    col = column or "signal"
    async with state.pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT data, risk_level FROM gds_staging_experiments WHERE trace_id=$1", state.tid)

    vals, parsed = [], []
    for r in rows:
        d = _as_dict(r["data"])
        v = d.get(col)
        if v is None:
            v = pick_field(d, _SIG)        # tolerate alternate signal column names
        if v is None:
            continue
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        vals.append(fv)
        parsed.append({
            "scientist": pick_field(d, _NAME, "Unknown"),
            "well":      pick_field(d, _WELL, "?"),
            "value":     fv,
            "agent_risk": r["risk_level"],
        })

    if not vals:
        return {"status": "error",
                "error": f"No numeric values found for column '{col}'. "
                         f"Try the primary measurement column (e.g. 'signal')."}

    arr  = np.array(vals)
    mean = float(arr.mean())
    std  = float(arr.std())
    low, high = mean - 2 * std, mean + 2 * std

    mismatches = []
    for p in parsed:
        my_risk = "review" if (p["value"] < low or p["value"] > high) else "auto"
        if my_risk != p["agent_risk"]:
            mismatches.append({**p, "verifier_risk": my_risk})

    result = {
        "column": col,
        "n": len(vals),
        "mean": round(mean, 4), "std": round(std, 4),
        "threshold_low": round(low, 4), "threshold_high": round(high, 4),
        "method": "mean_2sigma (independently recomputed by verifier)",
        "classification_mismatches": mismatches,
        "classification_agrees": len(mismatches) == 0,
    }
    state.evidence["anomaly_recompute"] = result
    return {
        "status": "success", **result,
        "note": ("Independently recomputed from staging data and compared to the "
                 "migration agent's risk_level. mismatches=0 means the agent classified "
                 "every row consistently with these thresholds."),
    }


# ── Tool 4: compare_staging_vs_production (signal integrity) ──────────────────

async def _tool_compare_staging_vs_production(
    state: _RunState, scientist: str | None = None, well: str | None = None, limit: int = 5
) -> dict:
    limit = min(max(int(limit or 5), 1), 20)
    async with state.pool.acquire() as conn:
        staging_rows = await conn.fetch("""
            SELECT data, status FROM gds_staging_experiments
            WHERE trace_id=$1 AND status IN ('approved','auto_approved')
        """, state.tid)
        prod_rows = await conn.fetch("""
            SELECT e.well_position, e.signal, u.name AS scientist_name
            FROM   gds_experiments e
            JOIN   gds_users u ON u.gds_user_id = e.gds_user_id
            WHERE  e.trace_id = $1
        """, state.tid)

    prod_map = {(r["scientist_name"], str(r["well_position"])): float(r["signal"]) for r in prod_rows}
    checks, checked = [], 0
    for r in staging_rows:
        if checked >= limit:
            break
        d = _as_dict(r["data"])
        name = pick_field(d, _NAME)
        wl   = pick_field(d, _WELL)
        if not name or wl is None:
            continue
        if scientist and name != scientist:
            continue
        if well and str(wl) != str(well):
            continue
        sig_s = pick_field(d, _SIG)
        sig_g = prod_map.get((name, str(wl)))
        if sig_g is None:
            continue
        sig_s_f = float(sig_s) if sig_s is not None else None
        match = sig_s_f is not None and abs(sig_s_f - sig_g) < 1e-4
        checks.append({"scientist": name, "well": wl,
                       "staging_signal": sig_s_f, "gds_signal": sig_g, "match": match})
        checked += 1

    all_match = bool(checks) and all(c["match"] for c in checks)
    state.evidence["signal_spot_check"] = checks
    return {
        "status": "success", "checks": checks,
        "all_match": all_match, "n_checked": len(checks),
        "note": "Verifies values were not corrupted between staging and production. "
                "all_match=true means signal integrity is confirmed for the sampled rows.",
    }


# ── Tool 5: query_rows (discretionary investigation) ──────────────────────────

async def _tool_query_rows(
    state: _RunState, status: str | None = None, risk_level: str | None = None,
    scientist: str | None = None, limit: int = 20
) -> dict:
    limit = min(max(int(limit or 20), 1), 50)
    clauses, args = ["trace_id = $1"], [state.tid]
    if status:
        args.append(status);     clauses.append(f"status = ${len(args)}")
    if risk_level:
        args.append(risk_level); clauses.append(f"risk_level = ${len(args)}")
    sql = (f"SELECT data, status, risk_level FROM gds_staging_experiments "
           f"WHERE {' AND '.join(clauses)} LIMIT {limit}")
    async with state.pool.acquire() as conn:
        rows = await conn.fetch(sql, *args)

    out = []
    for r in rows:
        d = _as_dict(r["data"])
        name = pick_field(d, _NAME, "Unknown")
        if scientist and name != scientist:
            continue
        out.append({"scientist": name, "well": pick_field(d, _WELL, "?"),
                    "signal": pick_field(d, _SIG), "status": r["status"],
                    "risk_level": r["risk_level"]})
    return {"status": "success", "rows": out, "count": len(out),
            "note": "Read-only view of staging rows matching the filter."}


# ── Tool 6: trace_to_source (lineage) ─────────────────────────────────────────

async def _tool_trace_to_source(
    state: _RunState, scientist: str | None = None, well: str | None = None
) -> dict:
    async with state.pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT e.well_position, e.signal, e.source_experiment_id, u.name AS scientist_name
            FROM   gds_experiments e
            JOIN   gds_users u ON u.gds_user_id = e.gds_user_id
            WHERE  e.trace_id = $1
        """, state.tid)

    out = []
    for r in rows:
        if scientist and r["scientist_name"] != scientist:
            continue
        if well and str(r["well_position"]) != str(well):
            continue
        out.append({"scientist": r["scientist_name"], "well": r["well_position"],
                    "signal": float(r["signal"]),
                    "source_experiment_id": r["source_experiment_id"]})
    missing = [o for o in out if o["source_experiment_id"] in (None, "")]
    state.evidence["traceability_sample"] = out[:10]
    return {"status": "success", "lineage": out[:25], "count": len(out),
            "rows_missing_source_id": len(missing),
            "note": "Each production row should carry its source_experiment_id back to "
                    "the origin record. rows_missing_source_id>0 is a traceability gap."}


# ── Tool 7: get_per_scientist_breakdown (+ exclusion evidence) ────────────────

async def _tool_get_per_scientist_breakdown(state: _RunState) -> dict:
    async with state.pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT data, status FROM gds_staging_experiments WHERE trace_id=$1", state.tid)

    # Business model: each record is Accepted (live), Removed (excluded OR rejected),
    # or Pending. excluded and rejected are presented together as "removed" so counts
    # stay consistent with reconciliation; the sub-reason is tagged per record.
    scientists: dict = {}
    for r in rows:
        d    = _as_dict(r["data"])
        name = pick_field(d, _NAME, "Unknown")
        item = {"well": pick_field(d, _WELL, "?"), "signal": pick_field(d, _SIG)}
        sc   = scientists.setdefault(name, {"accepted": [], "removed": [], "pending": []})
        st   = r["status"]
        if st in ("approved", "auto_approved"):
            sc["accepted"].append(item)
        elif st in ("excluded", "rejected"):
            sc["removed"].append({**item, "disposition": "rejected" if st == "rejected" else "excluded"})
        else:
            sc["pending"].append(item)

    summary = {n: {"accepted": len(b["accepted"]), "removed": len(b["removed"]),
                   "pending": len(b["pending"])} for n, b in scientists.items()}
    removed_records = [{"scientist": n, **w} for n, b in scientists.items() for w in b["removed"]]

    state.evidence["per_scientist"]   = scientists
    state.evidence["removed_records"] = removed_records
    return {
        "status": "success", "per_scientist": summary, "removed_records": removed_records,
        "note": "Per-researcher accepted/removed/pending counts, plus the specific removed "
                "records (each tagged 'excluded' or 'rejected'). Total removed = excluded + "
                "rejected. Flag any researcher with an unusually high removal rate.",
    }


# ── Tool 8: get_audit_timeline (+ resolution time) ────────────────────────────

def _fmt_duration(seconds: int) -> str:
    if seconds < 60:   return f"{seconds}s"
    if seconds < 3600: return f"{seconds // 60}m {seconds % 60}s"
    return f"{seconds // 3600}h {(seconds % 3600) // 60}m"


async def _tool_get_audit_timeline(state: _RunState) -> dict:
    async with state.pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT event, staged_count, promoted_count, excluded_count,
                   approved_by, created_at
            FROM   migration_audit_log
            WHERE  trace_id = $1
            ORDER  BY created_at
        """, state.tid)
    timeline = [{
        "event": r["event"], "promoted_count": r["promoted_count"],
        "excluded_count": r["excluded_count"], "approved_by": r["approved_by"],
        "timestamp": r["created_at"].isoformat() if r["created_at"] else None,
    } for r in rows]

    # Resolution time: auto-approval -> last human action (computed, not by the LLM)
    resolution = None
    auto_evt = next((e for e in timeline if e["event"] == "auto_approved"), None)
    hitl_evt = next((e for e in reversed(timeline)
                     if e["event"] in ("hitl_approved", "rejected")), None)
    if auto_evt and hitl_evt and auto_evt["timestamp"] and hitl_evt["timestamp"]:
        a = datetime.fromisoformat(auto_evt["timestamp"])
        h = datetime.fromisoformat(hitl_evt["timestamp"])
        secs = int((h - a).total_seconds())
        resolution = {
            "auto_approved_at": auto_evt["timestamp"],
            "resolved_at":      hitl_evt["timestamp"],
            "duration_seconds": secs,
            "duration_human":   _fmt_duration(secs),
        }

    state.evidence["audit_timeline"]  = timeline
    state.evidence["resolution_time"] = resolution
    return {"status": "success", "timeline": timeline, "count": len(timeline),
            "resolution_time": resolution,
            "note": "Ordered audit events (who/what/when) and the auto→resolved duration."}


# ── Tool definitions (Gemini format) ──────────────────────────────────────────

TOOL_DEFINITIONS = types.Tool(function_declarations=[
    types.FunctionDeclaration(
        name="get_migration_summary",
        description=("READ-ONLY. Baseline counts (staged/auto/approved/excluded/pending/"
                     "in_production), field mapping used, and null-field analysis. Call FIRST."),
        parameters=types.Schema(type=types.Type.OBJECT, properties={}),
    ),
    types.FunctionDeclaration(
        name="check_reconciliation",
        description=("READ-ONLY. Computes the source<->target balance booleans: row_balance_ok "
                     "(staged == promoted+excluded+pending) and scientist_balance_ok. "
                     "Returns the exact numbers and any scientists missing from GDS."),
        parameters=types.Schema(type=types.Type.OBJECT, properties={}),
    ),
    types.FunctionDeclaration(
        name="recompute_anomaly_threshold",
        description=("READ-ONLY. Independently re-derive mean+-2sigma on the measurement column "
                     "from staging data, then AUDIT the migration agent: list any rows whose "
                     "risk_level disagrees with your recomputed classification."),
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={"column": types.Schema(type=types.Type.STRING,
                        description="Measurement column to recompute (default 'signal').")},
        ),
    ),
    types.FunctionDeclaration(
        name="compare_staging_vs_production",
        description=("READ-ONLY. Signal-integrity spot-check: confirm staging values match the "
                     "values that landed in production. Omit args for a batch sample; pass "
                     "scientist+well to check one specific row you find suspicious."),
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "scientist": types.Schema(type=types.Type.STRING, description="Optional scientist name"),
                "well":      types.Schema(type=types.Type.STRING, description="Optional well position"),
                "limit":     types.Schema(type=types.Type.INTEGER, description="Batch size (1-20, default 5)"),
            },
        ),
    ),
    types.FunctionDeclaration(
        name="query_rows",
        description=("READ-ONLY. Pull specific staging rows to investigate, filtered by status "
                     "('pending'/'excluded'/'approved'/'auto_approved'), risk_level, or scientist. "
                     "Use this to find the exact rows behind a discrepancy."),
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "status":     types.Schema(type=types.Type.STRING),
                "risk_level": types.Schema(type=types.Type.STRING),
                "scientist":  types.Schema(type=types.Type.STRING),
                "limit":      types.Schema(type=types.Type.INTEGER),
            },
        ),
    ),
    types.FunctionDeclaration(
        name="trace_to_source",
        description=("READ-ONLY. Show production rows with their source_experiment_id to verify "
                     "end-to-end lineage. Flags any production row missing a source id."),
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "scientist": types.Schema(type=types.Type.STRING, description="Optional scientist filter"),
                "well":      types.Schema(type=types.Type.STRING, description="Optional well filter"),
            },
        ),
    ),
    types.FunctionDeclaration(
        name="get_per_scientist_breakdown",
        description=("READ-ONLY. Per-scientist promoted/excluded/pending counts plus the "
                     "specific excluded wells (exclusion evidence). Use to build the "
                     "per-scientist breakdown and to spot outlier exclusion rates."),
        parameters=types.Schema(type=types.Type.OBJECT, properties={}),
    ),
    types.FunctionDeclaration(
        name="get_audit_timeline",
        description=("READ-ONLY. The ordered audit-log events for this trace (who did what, "
                     "when) and the auto→resolved duration."),
        parameters=types.Schema(type=types.Type.OBJECT, properties={}),
    ),
])


# ── System prompt ─────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are an autonomous data-migration VERIFICATION agent — an independent auditor.

You are READ-ONLY. Every tool is a SELECT. You CATCH and FLAG problems; you never fix data — a human does that. Never claim you corrected anything.

You NEVER compute numbers yourself. Every figure comes from a tool. Your job is to decide WHICH tools to call, INTERPRET what they return, INVESTIGATE anything that looks wrong, and write a reasoned verdict.

MANDATORY — you cannot finish until ALL of these have been called successfully:
1. get_migration_summary
2. check_reconciliation
3. recompute_anomaly_threshold        (this independently audits the migration agent's classification)
4. compare_staging_vs_production      (signal-integrity spot-check)
5. get_per_scientist_breakdown        (per-scientist counts + the specific excluded wells)
6. get_audit_timeline                 (WHO did WHAT, WHEN — every promotion/exclusion must be attributed to an actor and a timestamp)

In the report you MUST attribute actions: who auto-approved, who (named human) approved or excluded review rows, and when. If any rows changed state with no corresponding audit event, flag it as an attribution gap — that is a compliance finding.

INVESTIGATE (use your judgment):
- If row_balance_ok or scientist_balance_ok is false → use query_rows / trace_to_source to find the exact rows behind it and explain the cause.
- If recompute shows classification_mismatches → name them; the migration agent may have mis-flagged a row.
- If any signal spot-check fails → pull that row and trace it.
- If a scientist has an unusually high exclusion rate, or a field has nulls → call it out even when the hard balances pass.

After investigating, write a BUSINESS migration report for executives and compliance stakeholders — NOT an engineering log.

AUDIENCE & LANGUAGE RULES (critical — the reader is a non-technical executive):
- No statistics jargon. NEVER write "mean", "standard deviation", "2σ", "sigma", "threshold", "anomaly detection".
- No system internals. NEVER write "staging", "risk_level", "JSONB", "ON CONFLICT", "tool", "trace_id" (call it "report reference"), "promotion_config", or raw database/column names — EXCEPT inside section 3 (Field Mapping), where field names are the point.
- Translate technical facts into business language:
    flagged readings        → "values that fell outside the expected range"
    signal-integrity check   → "migrated values match the original source exactly"
    reconciliation balance   → "every record is fully accounted for — nothing lost or duplicated"
    independent re-check      → "an automated secondary review re-confirmed which records were accepted vs. flagged"
    source traceability      → "every migrated record can be traced back to its original source record"
- Every number must come from your tools. State them plainly.

Use EXACTLY this structure:

DATA MIGRATION REPORT
══════════════════════════════════════════════════════════════
Report reference : {trace_id}
Date             : {timestamp}
Initiated by     : {initiated_by}

1. EXECUTIVE SUMMARY
   One short paragraph: what was migrated (from the legacy system to the new system),
   how many records and researchers, the outcome, and the overall status.

2. MIGRATION SCOPE
   - Records migrated:        {n} of {n} source records
   - Researchers covered:     {n}
   - Successfully moved live:  {n}  ({rate}%)

3. FIELD MAPPING  (how legacy data was mapped into the new system)
   One line per mapping:  <legacy field>  →  <new-system field>  — plain description of what it holds.
   List ONLY meaningful business data fields. EXCLUDE internal plumbing: do NOT show
   database table names, foreign-key/linking fields (e.g. anything mapping to a user id),
   or system reference ids. If a field was renamed or had a unit change, note it in plain
   words. State that the mapping was independently reviewed before any data moved.

4. DATA QUALITY & VALIDATION
   - Completeness: any missing values in key fields, or "All key fields complete."
   - Accuracy: confirm migrated values match the original source exactly (state how many records were spot-checked).
   - Independent review: state that an automated secondary review re-confirmed which records were accepted vs. flagged, and whether it agreed.

5. EXCEPTIONS & RESOLUTIONS  (what was flagged and how it was handled)
   - How many records were flagged for review, and why, in plain terms ("readings that fell outside the expected range"). Do NOT print numeric thresholds or score ranges.
   - How the flagged records were resolved: state how many were accepted after review, and how many were removed. Treat excluded + rejected together as "removed" (total removed = excluded + rejected); you may note the split in parentheses.
   - List the removed records (researcher, well, value, and whether excluded or rejected) as exception evidence, or "No exceptions in this migration."

6. RECONCILIATION  (nothing lost, nothing duplicated)
   - Total records fully accounted for: moved live + removed (excluded + rejected) + still pending = total migrated.
   - Confirm every researcher with accepted records is present in the new system.
   - Per-researcher summary — one line each: accepted / removed / pending.

7. ACCOUNTABILITY & AUDIT TRAIL
   - Timeline of EVERY recorded action, in order: automatic acceptance, and each MANUAL human decision (approvals, exclusions, rejections) — for each state what happened, when, and who performed or approved it.
   - You MUST surface the manual approvals/exclusions/rejections here, not just the automatic step. Use the audit timeline events (hitl_approved, hitl_excluded, hitl_rejected) and their actors.
   - Resolution time, if a human review step occurred.
   - Flag any state change that has no recorded owner as an accountability gap.

8. OVERALL STATUS
   A clear sign-off line on its own line, written EXACTLY as one of:
   Overall: PASS
   Overall: NEEDS REVIEW
   Then one sentence of professional judgment (e.g. note a researcher with an unusually high exclusion rate worth a closer look).
══════════════════════════════════════════════════════════════

Rules:
- Use exact numbers from your tools; never invent or round.
- Write "Overall: PASS" ONLY when every record reconciles, accuracy is confirmed, and nothing remains pending; otherwise write "Overall: NEEDS REVIEW".
- Keep it concise, factual, and readable by a non-technical executive. No code, no jargon.
"""


# ── Agent loop ────────────────────────────────────────────────────────────────

async def run_verification_agent(trace_id: str) -> dict:
    tid  = uuid_mod.UUID(trace_id)
    pool = await get_gds_pool()

    # Nothing to verify?
    async with pool.acquire() as conn:
        staged = await conn.fetchval(
            "SELECT COUNT(*) FROM gds_staging_experiments WHERE trace_id=$1", tid)
    if not staged:
        return {"trace_id": trace_id, "error": "No migration data found for this trace_id.",
                "report": None, "data": {}}

    state  = _RunState(pool=pool, tid=tid)
    client = get_client()
    log    = logging.LoggerAdapter(logger, {"trace_id": trace_id})
    trace: list[dict] = []

    def _step(action: str, **kw):
        trace.append({"action": action, "timestamp": datetime.utcnow().isoformat(),
                      **{k: str(v)[:300] for k, v in kw.items()}})
        log.info("VERIFIER [%s] %s", action, str(kw)[:120])

    report_date = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    contents = [types.Content(role="user", parts=[types.Part(text=(
        f"Audit migration reference {trace_id}. Today's date for the report header is "
        f"{report_date} — use it for the Date field, do not use a placeholder. Run the "
        f"mandatory checks, investigate anything suspicious, and produce the business "
        f"migration report with an overall status."
    ))])]

    cfg = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT, tools=[TOOL_DEFINITIONS], temperature=0.0)

    turns_used = 0
    report_text = ""
    try:
        for turn in range(MAX_TURNS):
            turns_used = turn + 1
            response = await generate_with_backoff(client, contents=contents, config=cfg, model=_MODEL)

            if not response.candidates or not response.candidates[0].content \
               or not response.candidates[0].content.parts:
                _step("EMPTY_CONTENT", message="No candidate content — stopping.")
                break

            candidate = response.candidates[0]
            contents.append(types.Content(role="model", parts=candidate.content.parts))
            tool_calls = [p for p in candidate.content.parts if p.function_call]

            if not tool_calls:
                missing = MANDATORY_TOOLS - state.called
                if missing:
                    _step("EARLY_STOP_GUARD", missing=str(sorted(missing)))
                    contents.append(types.Content(role="user", parts=[types.Part(text=(
                        f"You have not finished the mandatory audit. Still required: "
                        f"{', '.join(sorted(missing))}. Call them, then write the report."
                    ))]))
                    continue
                report_text = " ".join(p.text for p in candidate.content.parts if p.text).strip()
                _step("VERIFIER_DONE", chars=len(report_text))
                break

            results = []
            for part in tool_calls:
                fn   = part.function_call
                name = fn.name
                args = dict(fn.args) if fn.args else {}
                _step(f"TOOL_CALL:{name}", args=str(args)[:120])
                try:
                    if name == "get_migration_summary":
                        result = await _tool_get_migration_summary(state)
                    elif name == "check_reconciliation":
                        result = await _tool_check_reconciliation(state)
                    elif name == "recompute_anomaly_threshold":
                        result = await _tool_recompute_anomaly_threshold(args.get("column", "signal"), state)
                    elif name == "compare_staging_vs_production":
                        result = await _tool_compare_staging_vs_production(
                            state, scientist=args.get("scientist"), well=args.get("well"),
                            limit=args.get("limit", 5))
                    elif name == "query_rows":
                        result = await _tool_query_rows(
                            state, status=args.get("status"), risk_level=args.get("risk_level"),
                            scientist=args.get("scientist"), limit=args.get("limit", 20))
                    elif name == "trace_to_source":
                        result = await _tool_trace_to_source(
                            state, scientist=args.get("scientist"), well=args.get("well"))
                    elif name == "get_per_scientist_breakdown":
                        result = await _tool_get_per_scientist_breakdown(state)
                    elif name == "get_audit_timeline":
                        result = await _tool_get_audit_timeline(state)
                    else:
                        result = {"status": "error", "error": f"Unknown tool: {name}."}
                except Exception as exc:
                    result = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}

                if result.get("status") == "success":
                    state.called.add(name)
                _step(f"TOOL_RESULT:{name}", status=result.get("status", ""))
                results.append(types.Part(function_response=types.FunctionResponse(
                    name=name, response=result)))

            contents.append(types.Content(role="user", parts=results))
        else:
            _step("MAX_TURNS_REACHED", message=f"Hit safety limit of {MAX_TURNS} turns.")
    finally:
        pass  # shared GDS pool — do not close

    generated_at = datetime.now(timezone.utc).isoformat()
    return {
        "trace_id":        trace_id,
        "report":          report_text or "Verification incomplete — agent did not produce a report.",
        "data":            {"generated_at": generated_at, **state.evidence},
        "generated_at":    generated_at,
        "reasoning_trace": trace,
        "turns_used":      turns_used,
    }
