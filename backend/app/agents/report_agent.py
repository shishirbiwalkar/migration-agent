"""
Verification Report Agent
==========================
Gathers all migration evidence from the DB in Python, passes it as structured
context to Gemini in a single call. Gemini writes the human-readable report.
"""

import json
import logging
import uuid as uuid_mod
from datetime import datetime, timezone

from google.genai import types

from app.connectors import get_gds_pool
from app.core.llm import get_client, generate_with_backoff, MODEL as _MODEL
from app.core.mapping import NAME_FIELDS as _NAME, WELL_FIELDS as _WELL, SIGNAL_FIELDS as _SIG

logger = logging.getLogger(__name__)


# ── Data gathering ────────────────────────────────────────────────────────────

async def _gather_report_data(trace_id: str) -> dict:
    pool = await get_gds_pool()
    tid  = uuid_mod.UUID(trace_id)

    async with pool.acquire() as conn:

        # Audit timeline
        audit_rows = await conn.fetch("""
            SELECT event, staged_count, promoted_count, excluded_count,
                   approved_by, created_at
            FROM   migration_audit_log
            WHERE  trace_id = $1
            ORDER  BY created_at
        """, tid)

        # Promotion config + initiator
        plan_row = await conn.fetchrow(
            "SELECT plan_json, initiated_by FROM migration_plans WHERE trace_id=$1", tid)
        config     = {}
        initiated_by = "Unknown"
        if plan_row:
            config       = json.loads(plan_row["plan_json"]) \
                           if isinstance(plan_row["plan_json"], str) \
                           else plan_row["plan_json"]
            initiated_by = plan_row["initiated_by"] or "Unknown"

        # Staging rows — all statuses
        staging_rows = await conn.fetch("""
            SELECT data, status, risk_level, created_at
            FROM   gds_staging_experiments
            WHERE  trace_id = $1
        """, tid)

        # Staging counts by status + risk_level
        staging_counts = await conn.fetch("""
            SELECT status, risk_level, COUNT(*) AS cnt
            FROM   gds_staging_experiments
            WHERE  trace_id = $1
            GROUP  BY status, risk_level
        """, tid)

        # Production rows for this trace
        prod_rows = await conn.fetch("""
            SELECT e.well_position, e.signal, e.source_experiment_id,
                   u.name AS scientist_name
            FROM   gds_experiments e
            JOIN   gds_users u ON u.gds_user_id = e.gds_user_id
            WHERE  e.trace_id = $1
        """, tid)

        # Entity counts in GDS for this trace
        gds_user_count = await conn.fetchval("""
            SELECT COUNT(DISTINCT u.gds_user_id)
            FROM   gds_users u
            JOIN   gds_experiments e ON e.gds_user_id = u.gds_user_id
            WHERE  e.trace_id = $1
        """, tid)

    # ── Build count matrix ────────────────────────────────────────────────────
    counts_raw: dict = {}
    for r in staging_counts:
        counts_raw[f"{r['risk_level']}_{r['status']}"] = int(r["cnt"])

    total_staged    = sum(counts_raw.values())
    auto_approved   = counts_raw.get("auto_auto_approved", 0)
    review_approved = counts_raw.get("review_approved", 0)
    review_excluded = counts_raw.get("review_excluded", 0)
    review_pending  = counts_raw.get("review_pending", 0)
    in_production   = int(prod_rows.__len__())

    # ── Per-scientist breakdown ───────────────────────────────────────────────
    scientists: dict = {}
    for r in staging_rows:
        data   = r["data"] if isinstance(r["data"], dict) else json.loads(r["data"])
        name   = next((data[k] for k in _NAME if data.get(k)), "Unknown")
        well   = next((data[k] for k in _WELL if data.get(k)), "?")
        sig    = next((data[k] for k in _SIG  if data.get(k) is not None), None)

        if name not in scientists:
            scientists[name] = {"approved": [], "excluded": [], "pending": []}

        bucket = r["status"]
        if bucket in ("approved", "auto_approved"):
            scientists[name]["approved"].append({"well": well, "signal": sig})
        elif bucket == "excluded":
            scientists[name]["excluded"].append({"well": well, "signal": sig})
        else:
            scientists[name]["pending"].append({"well": well, "signal": sig})

    # ── Null field counts ─────────────────────────────────────────────────────
    field_null_counts: dict = {}
    field_total_counts: dict = {}
    for r in staging_rows:
        data = r["data"] if isinstance(r["data"], dict) else json.loads(r["data"])
        for key, val in data.items():
            field_total_counts[key] = field_total_counts.get(key, 0) + 1
            if val is None or val == "" or val == "null":
                field_null_counts[key] = field_null_counts.get(key, 0) + 1

    null_report = {}
    for key, total in field_total_counts.items():
        nulls = field_null_counts.get(key, 0)
        null_report[key] = {
            "total": total,
            "nulls": nulls,
            "null_pct": round(nulls / total * 100, 1) if total else 0,
        }

    # ── Signal spot-check — staging value vs GDS value ───────────────────────
    spot_check = []
    prod_map = {(r["scientist_name"], r["well_position"]): r for r in prod_rows}

    checked = 0
    for r in staging_rows:
        if checked >= 5: break
        if r["status"] not in ("approved", "auto_approved"): continue
        data   = r["data"] if isinstance(r["data"], dict) else json.loads(r["data"])
        name   = next((data[k] for k in _NAME if data.get(k)), None)
        well   = next((data[k] for k in _WELL if data.get(k)), None)
        sig_s  = next((data[k] for k in _SIG  if data.get(k) is not None), None)
        if not name or not well: continue

        prod = prod_map.get((name, well))
        if prod:
            sig_g = float(prod["signal"])
            sig_s_f = float(sig_s) if sig_s is not None else None
            match = (sig_s_f is not None and abs(sig_s_f - sig_g) < 0.0001)
            spot_check.append({
                "scientist":      name,
                "well":           well,
                "staging_signal": sig_s_f,
                "gds_signal":     sig_g,
                "match":          match,
            })
            checked += 1

    # ── Source traceability ───────────────────────────────────────────────────
    traceability_sample = []
    for r in prod_rows[:5]:
        traceability_sample.append({
            "scientist":           r["scientist_name"],
            "well":                r["well_position"],
            "source_experiment_id": r["source_experiment_id"],
        })

    # ── Source vs target reconciliation ──────────────────────────────────────
    # The staging table is the immutable snapshot of what was extracted from the
    # source at migration time. We reconcile against THAT, not the live source DB —
    # fully-migrated scientists are deleted from the source by design, so a live
    # source query would always under-count and produce a false mismatch.
    staged_scientists   = set()
    for name, buckets in scientists.items():
        if name != "Unknown":
            staged_scientists.add(name)

    promoted_scientists = {name for name, b in scientists.items()
                           if b["approved"] and name != "Unknown"}
    gds_scientists      = {r["scientist_name"] for r in prod_rows}

    row_balance_ok      = total_staged == (in_production + review_excluded + review_pending)
    scientist_balance_ok = promoted_scientists == gds_scientists

    source_reconciliation = {
        "staged_experiments":   total_staged,
        "promoted_to_gds":      in_production,
        "excluded":             review_excluded,
        "pending":              review_pending,
        "row_balance_ok":       row_balance_ok,
        "staged_scientists":    len(staged_scientists),
        "scientists_in_gds":    len(gds_scientists),
        "scientist_balance_ok": scientist_balance_ok,
        "note": "Reconciled against the staging snapshot (source-of-truth at extraction time). "
                "Source records are deleted post-migration by design.",
    }

    # ── Resolution time ───────────────────────────────────────────────────────
    resolution_time = None
    timeline_events = [dict(r) for r in audit_rows]
    auto_event  = next((e for e in timeline_events if e["event"] == "auto_approved"), None)
    hitl_event  = next((e for e in timeline_events if e["event"] == "hitl_approved"), None)
    if auto_event and hitl_event:
        delta = hitl_event["created_at"] - auto_event["created_at"]
        resolution_time = {
            "auto_approved_at":  auto_event["created_at"].isoformat(),
            "hitl_approved_at":  hitl_event["created_at"].isoformat(),
            "duration_seconds":  int(delta.total_seconds()),
            "duration_human":    _fmt_duration(int(delta.total_seconds())),
        }

    # ── Field mapping ─────────────────────────────────────────────────────────
    field_mapping = []
    for src, tgt in config.get("entity_table",  {}).get("column_map", {}).items():
        field_mapping.append({"source": src, "target": tgt,
                               "table": config.get("entity_table", {}).get("name", "")})
    for src, tgt in config.get("records_table", {}).get("column_map", {}).items():
        field_mapping.append({"source": src, "target": tgt,
                               "table": config.get("records_table", {}).get("name", "")})

    # ── Audit timeline ────────────────────────────────────────────────────────
    timeline = []
    for r in audit_rows:
        timeline.append({
            "event":          r["event"],
            "promoted_count": r["promoted_count"],
            "excluded_count": r["excluded_count"],
            "approved_by":    r["approved_by"],
            "timestamp":      r["created_at"].isoformat() if r["created_at"] else None,
        })

    return {
        "trace_id":     trace_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "initiated_by": initiated_by,
        "counts": {
            "total_staged":   total_staged,
            "auto_approved":  auto_approved,
            "review_flagged": review_approved + review_excluded + review_pending,
            "hitl_approved":  review_approved,
            "excluded":       review_excluded,
            "pending":        review_pending,
            "in_production":  in_production,
            "migration_rate_pct": round(in_production / total_staged * 100, 1)
                                  if total_staged else 0,
        },
        "field_mapping":          field_mapping,
        "anomaly_thresholds":     config.get("anomaly_thresholds", {}),
        "per_scientist":          scientists,
        "null_field_counts":      null_report,
        "signal_spot_check":      spot_check,
        "source_reconciliation":  source_reconciliation,
        "traceability_sample":    traceability_sample,
        "resolution_time":        resolution_time,
        "audit_timeline":         timeline,
    }


def _fmt_duration(seconds: int) -> str:
    if seconds < 60:   return f"{seconds}s"
    if seconds < 3600: return f"{seconds // 60}m {seconds % 60}s"
    return f"{seconds // 3600}h {(seconds % 3600) // 60}m"


# ── System prompt ─────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a data migration verification specialist producing a formal compliance report.

Generate a report using EXACTLY this structure — fill every section with real data:

MIGRATION VERIFICATION REPORT
══════════════════════════════════════════════════════════════
Trace ID     : {trace_id}
Generated    : {timestamp}
Initiated by : {initiated_by}

── DATA VOLUME ──────────────────────────────────────────────
Total staged        : {n}
Auto-approved       : {n}   (clean signals — AI Agent, no human review)
Flagged for review  : {n}   (anomalous signals — required human decision)
  → Human approved  : {n}
  → Excluded        : {n}
  → Still pending   : {n}
Final in production : {n}
Migration rate      : {pct}%

── SOURCE vs TARGET RECONCILIATION ──────────────────────────
(Reconciled against the staging snapshot — the source-of-truth captured at
 extraction time. Source records are deleted post-migration by design.)
Staged experiments    : {n}
Promoted to GDS       : {n}
Excluded              : {n}
Pending               : {n}
Row balance           : {✅ OK / ❌ Off}   (staged = promoted + excluded + pending)
Scientists staged     : {n}
Scientists in GDS     : {n}
Scientist balance     : {✅ OK / ❌ Off}   (every promoted scientist is present in GDS)

── FIELD MAPPING USED ───────────────────────────────────────
{source_col}  →  {target_col}  ({table})
...

── DATA QUALITY — NULL FIELD ANALYSIS ───────────────────────
{field}   total={n}   nulls={n}   ({pct}%)
...   (only show fields with nulls > 0, or confirm "No null values detected")

── ANOMALY DETECTION ────────────────────────────────────────
Method    : mean ± 2σ
Column    : {col}
Threshold : low {low}  /  high {high}
Flagged   : {n} wells across {n} scientists

── SIGNAL INTEGRITY SPOT-CHECK ──────────────────────────────
(Verifies staging values were not corrupted during promotion)
{scientist} / {well}   staging={val}  →  GDS={val}   {✅ Match / ❌ Mismatch}
...

── SOURCE TRACEABILITY ──────────────────────────────────────
(ABASE experiment ID → GDS record)
{scientist} / {well}   source_experiment_id={id}
...   (or "Source IDs not captured — agent did not include source_experiment_id")

── RESOLUTION TIMELINE ──────────────────────────────────────
Auto-approved at  : {timestamp}
HITL approved at  : {timestamp}
Resolution time   : {duration}

── PER-SCIENTIST BREAKDOWN ──────────────────────────────────
{name}   {n} promoted  /  {n} excluded  /  {n} pending
...

── EXCLUSION EVIDENCE ───────────────────────────────────────
{scientist} / {well}   signal={value}   reason: anomalous signal  [excluded]
...   (or "No exclusions in this migration run")

── AUDIT TIMELINE ───────────────────────────────────────────
{timestamp}   {event}   {n} rows   by: {approver}
...

── VERIFICATION VERDICT ─────────────────────────────────────
{✅/❌} Row balance reconciled       (row_balance_ok == true)
{✅/❌} Scientist balance reconciled  (scientist_balance_ok == true)
{✅/❌} Signal integrity confirmed   (all spot-checks passed)
{✅/❌} No null values in key fields
{✅/❌} All anomalies accounted for  (flagged = approved + excluded + pending)
{✅/❌} Full audit trail present
{✅/❌} No pending rows remain       (migration fully resolved)

Overall: PASS or FAIL
══════════════════════════════════════════════════════════════

Rules:
- Use exact numbers from the provided data — never invent or round
- Verdict is FAIL if any ❌ check fails OR if pending > 0
- Row balance: ✅ only if row_balance_ok is true in the data
- Scientist balance: ✅ only if scientist_balance_ok is true in the data
- Signal integrity: ✅ only if ALL spot-checks show Match
- Keep it factual and precise — this is a compliance document
"""


# ── Report generation ─────────────────────────────────────────────────────────

async def run_report_agent(trace_id: str) -> dict:
    data = await _gather_report_data(trace_id)

    if not data["audit_timeline"] and data["counts"]["total_staged"] == 0:
        return {
            "trace_id": trace_id,
            "error":    "No migration data found for this trace_id.",
            "report":   None,
            "data":     data,
        }

    client = get_client()

    prompt = f"""Generate a verification report for this completed migration run.

MIGRATION DATA:
{json.dumps(data, indent=2, default=str)}

Follow the report template exactly. Use the actual numbers — do not approximate.
"""

    response = await generate_with_backoff(
        client,
        contents=[types.Content(role="user", parts=[types.Part(text=prompt)])],
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            temperature=0.1,
        ),
        model=_MODEL,
    )

    report_text = ""
    if response.candidates:
        report_text = " ".join(
            p.text for p in response.candidates[0].content.parts if p.text
        ).strip()

    logger.info("Report generated for trace=%s (%d chars)", trace_id, len(report_text))

    return {
        "trace_id":     trace_id,
        "report":       report_text,
        "data":         data,
        "generated_at": data["generated_at"],
    }
