"""
Review Resolution Agent — Per-Scientist HITL Resolution
=========================================================
Gemini-powered agent that resolves flagged wells for a specific scientist
based on the admin's natural language description of what the scientist communicated.

Key principle: Agent reasons about context — it does NOT pattern-match keywords.
The same resolution applies whether admin says "delete those wells and move scientist"
or "she confirmed it was a bad reading."

Tools:
  1. get_scientist_wells  — load staging picture (always called first)
  2. approve_well         — promote single well to production
  3. exclude_well         — mark single well excluded
  4. approve_all_wells    — promote all pending wells for the scientist
  5. exclude_all_wells    — exclude all pending wells for the scientist
"""

import os
import json
import logging
import traceback
import uuid as uuid_mod
from datetime import datetime

import asyncpg
from google.genai import types

from app.connectors import get_gds_pool
from app.core.llm import get_client, generate_with_backoff, MODEL as _MODEL
from app.core.mapping import (
    validate_identifier as _validate_identifier,
    coerce as _coerce,
    load_promotion_config as _load_config_conn,
    WELL_FIELDS, SIGNAL_FIELDS, pick_field,
)

logger   = logging.getLogger(__name__)
MAX_TURNS = 12

_NAME_FILTER = """(
    data->>'scientist_name' = $2
    OR data->>'name' = $2
    OR data->>'scientist' = $2
    OR data->>'user_name' = $2
)"""


# ── Config loader (pool-based wrapper around the shared conn-based loader) ─────

async def load_promotion_config(pool: asyncpg.Pool, tid: uuid_mod.UUID) -> dict:
    async with pool.acquire() as conn:
        return await _load_config_conn(conn, tid)


# ── Tool 1: get_scientist_wells ───────────────────────────────────────────────

async def _tool_get_scientist_wells(
    pool: asyncpg.Pool, tid: uuid_mod.UUID, scientist_name: str, config: dict
) -> dict:
    staging_tbl = config.get("staging_table", "gds_staging_experiments")
    async with pool.acquire() as conn:
        rows = await conn.fetch(f"""
            SELECT staging_id, data, risk_level, status, created_at
            FROM   {staging_tbl}
            WHERE  trace_id = $1 AND {_NAME_FILTER}
            ORDER  BY created_at
        """, tid, scientist_name)

    if not rows:
        return {"status": "not_found",
                "error": f"No staging rows found for '{scientist_name}' in trace {tid}."}

    wells = []
    for r in rows:
        data = r["data"] if isinstance(r["data"], dict) else json.loads(r["data"])

        well_pos = pick_field(data, WELL_FIELDS, "?")
        signal   = pick_field(data, SIGNAL_FIELDS)

        wells.append({
            "staging_id":    r["staging_id"],
            "well_position": well_pos,
            "signal":        signal,
            "risk_level":    r["risk_level"],
            "status":        r["status"],
        })

    pending  = [w for w in wells if w["status"] == "pending"]
    excluded = [w for w in wells if w["status"] == "excluded"]
    approved = [w for w in wells if w["status"] in ("approved", "auto_approved")]

    return {
        "status":          "success",
        "scientist_name":  scientist_name,
        "total_wells":     len(wells),
        "pending_wells":   len(pending),
        "excluded_wells":  len(excluded),
        "approved_wells":  len(approved),
        "wells":           wells,
        "note": "Use staging_id to approve or exclude specific wells. Only 'pending' wells can be acted on.",
    }


# ── Audit logging — every HITL action is attributed (who / what / when) ──────

async def _log_review_audit(pool, tid, event, approved_by, *, promoted=0, excluded=0):
    """
    Record a human-review action in the audit trail so it appears in the
    verification report. Best-effort: a logging failure never blocks the action.
    """
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO migration_audit_log
                       (trace_id, event, promoted_count, excluded_count, approved_by)
                   VALUES ($1, $2, $3, $4, $5)""",
                tid, event, promoted, excluded, approved_by)
    except Exception as e:
        logger.warning("Failed to write review audit event %s: %s", event, e)


# ── Per-row promotion (single staging row → production tables) ────────────────

async def _promote_single_row(
    pool: asyncpg.Pool, staging_id: int, tid: uuid_mod.UUID,
    config: dict, approved_by: str,
) -> dict:
    entity_cfg  = config.get("entity_table",  {})
    records_cfg = config.get("records_table", {})
    if not entity_cfg or not records_cfg:
        return {"status": "error", "error": "Promotion config incomplete."}

    entity_tbl  = _validate_identifier(entity_cfg["name"],       "entity_table.name")
    entity_map  = entity_cfg["column_map"]
    upsert_key  = entity_cfg["upsert_key"]
    entity_pk   = _validate_identifier(entity_cfg["pk"],         "entity_table.pk")
    if upsert_key in entity_map:
        upsert_key = entity_map[upsert_key]
    _validate_identifier(upsert_key, "entity_table.upsert_key")
    for col in entity_map.values():
        _validate_identifier(col, f"entity_table.column_map value '{col}'")

    records_tbl = _validate_identifier(records_cfg["name"],      "records_table.name")
    records_map = records_cfg["column_map"]
    fk_col      = _validate_identifier(records_cfg["fk_column"], "records_table.fk_column")
    upsert_keys = records_cfg["upsert_keys"]
    for col in upsert_keys:
        _validate_identifier(col, f"records_table.upsert_keys value '{col}'")
    for col in records_map.values():
        _validate_identifier(col, f"records_table.column_map value '{col}'")
    staging_tbl = _validate_identifier(
        config.get("staging_table", "gds_staging_experiments"), "staging_table")

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT * FROM {staging_tbl} WHERE staging_id=$1 AND status='pending'",
            staging_id,
        )
        if not row:
            return {"status": "error",
                    "error": f"Row {staging_id} not found or not pending."}

        data = row["data"] if isinstance(row["data"], dict) else json.loads(row["data"])

        async with conn.transaction():
            # Upsert entity (e.g. gds_users)
            entity_cols   = list(entity_map.values())
            try:
                entity_vals = [_coerce(data[src]) for src in entity_map.keys()]
            except KeyError as e:
                return {"status": "error", "error": f"Missing entity key {e}."}

            update_cols   = [c for c in entity_cols if c != upsert_key]
            update_clause = ", ".join(f"{c}=EXCLUDED.{c}" for c in update_cols)
            on_conflict   = f"DO UPDATE SET {update_clause}" if update_clause else "DO NOTHING"

            insert_entity = f"""
                INSERT INTO {entity_tbl} ({", ".join(entity_cols)})
                VALUES ({", ".join(f"${i+1}" for i in range(len(entity_cols)))})
                ON CONFLICT ({upsert_key}) {on_conflict}
                RETURNING {entity_pk}
            """
            ent_row   = await conn.fetchrow(insert_entity, *entity_vals)
            entity_id = ent_row[entity_pk]

            # Insert record (e.g. gds_experiments)
            _infra       = {"trace_id", "approved_by", fk_col, "source_experiment_id"}
            filtered_map = {src: tgt for src, tgt in records_map.items() if tgt not in _infra}
            rec_cols     = [fk_col] + list(filtered_map.values()) + ["trace_id", "approved_by"]
            try:
                rec_vals = [entity_id] + [_coerce(data[src]) for src in filtered_map.keys()] \
                           + [tid, approved_by]
            except KeyError as e:
                return {"status": "error", "error": f"Missing record key {e}."}

            src_exp_id = data.get("source_experiment_id")
            if src_exp_id is not None:
                rec_cols.append("source_experiment_id")
                rec_vals.append(str(src_exp_id))

            uk_clause  = ", ".join(f"{c}=EXCLUDED.{c}" for c in rec_cols
                                   if c not in upsert_keys)
            insert_rec = f"""
                INSERT INTO {records_tbl} ({", ".join(rec_cols)})
                VALUES ({", ".join(f"${i+1}" for i in range(len(rec_vals)))})
                ON CONFLICT ({", ".join(upsert_keys)}) DO UPDATE SET {uk_clause}
            """
            await conn.execute(insert_rec, *rec_vals)
            await conn.execute(
                f"UPDATE {staging_tbl} SET status='approved' WHERE staging_id=$1",
                staging_id,
            )

    well_pos = data.get("well_position", "?")
    return {"status": "success", "staging_id": staging_id,
            "action": "approved", "well_position": well_pos}


# ── Tool 2: approve_well ──────────────────────────────────────────────────────

async def _tool_approve_well(
    pool: asyncpg.Pool, staging_id: int, tid: uuid_mod.UUID,
    config: dict, approved_by: str,
) -> dict:
    return await _promote_single_row(pool, staging_id, tid, config, approved_by)


# ── Tool 3: exclude_well ──────────────────────────────────────────────────────

async def _tool_exclude_well(
    pool: asyncpg.Pool, staging_id: int, config: dict
) -> dict:
    staging_tbl = config.get("staging_table", "gds_staging_experiments")
    async with pool.acquire() as conn:
        result = await conn.execute(f"""
            UPDATE {staging_tbl}
            SET    status = 'excluded'
            WHERE  staging_id = $1 AND status = 'pending'
        """, staging_id)
    if int(result.split()[-1]) == 0:
        return {"status": "error", "error": f"Row {staging_id} not found or not pending."}
    return {"status": "success", "staging_id": staging_id, "action": "excluded"}


# ── Tool 4: approve_all_wells ─────────────────────────────────────────────────

async def _tool_approve_all_wells(
    pool: asyncpg.Pool, tid: uuid_mod.UUID, scientist_name: str,
    config: dict, approved_by: str,
) -> dict:
    staging_tbl = _validate_identifier(
        config.get("staging_table", "gds_staging_experiments"), "staging_table")
    async with pool.acquire() as conn:
        rows = await conn.fetch(f"""
            SELECT staging_id FROM {staging_tbl}
            WHERE  trace_id=$1 AND status='pending' AND {_NAME_FILTER}
        """, tid, scientist_name)

    if not rows:
        return {"status": "not_found", "error": f"No pending wells for '{scientist_name}'."}

    # Each _promote_single_row guards on `status='pending'`, so a row already taken
    # by a concurrent run promotes 0 and is reported as "already resolved" — never twice.
    approved, errors, already_taken = [], [], 0
    for r in rows:
        res = await _promote_single_row(pool, r["staging_id"], tid, config, approved_by)
        if res["status"] == "success":
            approved.append(res.get("well_position", str(r["staging_id"])))
        elif "not pending" in res.get("error", ""):
            already_taken += 1
        else:
            errors.append(res["error"])

    # If wells existed but none could be promoted, they were resolved elsewhere — report honestly
    if not approved and already_taken:
        return {"status": "noop", "approved_wells": [], "approved_count": 0,
                "already_resolved": already_taken,
                "note": f"{already_taken} well(s) were already resolved by another process."}

    return {"status": "success", "approved_wells": approved,
            "approved_count": len(approved),
            "already_resolved": already_taken, "errors": errors}


# ── Tool 5: exclude_all_wells ─────────────────────────────────────────────────

async def _tool_exclude_all_wells(
    pool: asyncpg.Pool, tid: uuid_mod.UUID, scientist_name: str, config: dict
) -> dict:
    staging_tbl = config.get("staging_table", "gds_staging_experiments")
    async with pool.acquire() as conn:
        result = await conn.execute(f"""
            UPDATE {staging_tbl}
            SET    status = 'excluded'
            WHERE  trace_id=$1 AND status='pending' AND {_NAME_FILTER}
        """, tid, scientist_name)
    count = int(result.split()[-1])
    return {"status": "success", "excluded_count": count,
            "note": f"All {count} pending wells for '{scientist_name}' excluded."}


# ── Batch tool: get_all_pending_wells ────────────────────────────────────────

async def _tool_get_all_pending_wells(
    pool: asyncpg.Pool, tid: uuid_mod.UUID, config: dict
) -> dict:
    """Return every flagged scientist and their pending wells in one shot."""
    staging_tbl = config.get("staging_table", "gds_staging_experiments")
    async with pool.acquire() as conn:
        rows = await conn.fetch(f"""
            SELECT staging_id, data, risk_level, status
            FROM   {staging_tbl}
            WHERE  trace_id = $1 AND risk_level = 'review'
            ORDER  BY data->>'scientist_name', staging_id
        """, tid)

    by_scientist: dict = {}
    for r in rows:
        data       = r["data"] if isinstance(r["data"], dict) else json.loads(r["data"])
        name       = (data.get("scientist_name") or data.get("user_name") or "unknown")
        well_pos   = pick_field(data, WELL_FIELDS, "?")
        signal     = pick_field(data, SIGNAL_FIELDS)
        entry = {
            "staging_id":    r["staging_id"],
            "well_position": well_pos,
            "signal":        signal,
            "status":        r["status"],
        }
        by_scientist.setdefault(name, []).append(entry)

    scientists = []
    for name, wells in by_scientist.items():
        pending  = [w for w in wells if w["status"] == "pending"]
        excluded = [w for w in wells if w["status"] == "excluded"]
        approved = [w for w in wells if w["status"] in ("approved", "auto_approved")]
        scientists.append({
            "scientist_name":  name,
            "pending_count":   len(pending),
            "excluded_count":  len(excluded),
            "approved_count":  len(approved),
            "wells":           wells,
        })

    total_pending = sum(s["pending_count"] for s in scientists)
    return {
        "status":        "success",
        "total_pending": total_pending,
        "scientists":    scientists,
        "note":          "Use staging_id to act on specific wells. Use scientist_name in approve_all_wells / exclude_all_wells.",
    }


# ── System prompt ─────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are the Review Resolution Agent for a scientific data migration pipeline.

Your job: resolve flagged experiment wells for a specific scientist based on the admin's description of what the scientist communicated.

═══ STEP 1 — ALWAYS LOAD CONTEXT FIRST ═══
Call get_scientist_wells before doing anything else. You must see the actual staging_ids, well positions, and signals before you can make any decision. Never skip this step.

═══ STEP 2 — INTERPRET THE MESSAGE ═══
Read the admin's description carefully. Map it to one of these intent categories:

APPROVE SPECIFIC WELLS — scientist identifies particular wells as valid:
  "B04 was a spike but it's real"  → approve just B04
  "everything except B03 is fine"  → approve all except B03 (exclude B03)
  "the instrument reset after A02"  → exclude A02, approve the rest

APPROVE ALL — scientist confirms all their data:
  "she confirmed everything is correct"
  "he said his data is fine, just outliers"
  "confirmed — please migrate all my readings"
  → approve_all_wells

EXCLUDE SPECIFIC WELLS — scientist identifies particular wells as bad:
  "B03 and B04 had calibration errors"  → exclude B03 and B04, approve the rest
  "well A02 is corrupted"               → exclude A02 only

EXCLUDE ALL — scientist objects to the entire migration:
  "do not migrate any of my data"
  "equipment failure — all readings are unreliable"
  "she withdrew consent for migration"
  → exclude_all_wells

═══ STEP 3 — HANDLE EDGE CASES ═══

ONLY 1 PENDING WELL — any message about "the reading", "the data", "it", "that well" unambiguously refers to the single pending well. Never ask for clarification when there is exactly 1 pending well:
  "Invalid reading"             → exclude the only pending well
  "She said it's fine"          → approve the only pending well
  "Equipment error"             → exclude the only pending well
  "Confirmed correct"           → approve the only pending well

SCIENTIST DISPUTES THE ANOMALY FLAG — their data was flagged as anomalous, but they say it's valid:
  Trust the scientist. They are the domain expert on their own experimental readings.
  Approve the disputed wells. Note in your summary that the anomaly flag was overridden by scientist judgment.

AMBIGUOUS MESSAGE — only applies when there are MULTIPLE pending wells and the message does not specify which ones:
  "She wasn't sure" with 3 pending wells → genuinely unclear, ask for clarification
  Do NOT approve or exclude anything in this case.
  Format: "Message unclear: [explain why]. Please confirm: [specific question]."
  NEVER ask for clarification when there is only 1 pending well.

NO PENDING WELLS — all wells already resolved:
  Report the current state and confirm no action is needed.

═══ STEP 4 — TAKE ACTIONS ═══
Execute the minimum operations needed. Use individual approve_well / exclude_well when only some wells are affected. Use approve_all_wells / exclude_all_wells only when the scientist's message applies to all their wells.

═══ STEP 5 — VERIFY ═══
After taking actions, call get_scientist_wells again to confirm the final state. Check that pending_wells matches what you expect.

═══ STEP 6 — WRITE YOUR SUMMARY ═══
End with a summary in this exact structure:

RESOLUTION SUMMARY
Scientist: [name]
Action taken: [one-line description]

Approved wells: [list well positions, or "none"]
Excluded wells: [list well positions, or "none"]
Reason: [one sentence — what the scientist communicated and why you acted as you did]
[If anomaly flag overridden: Note: Anomaly classification overridden by scientist judgment.]

This summary is shown directly to the admin and may be forwarded to the scientist.
"""

# ── Tool definitions ──────────────────────────────────────────────────────────

TOOL_DEFINITIONS = types.Tool(function_declarations=[
    types.FunctionDeclaration(
        name="get_scientist_wells",
        description="Load the current staging picture — wells, signals, statuses. Always call this first.",
        parameters=types.Schema(type=types.Type.OBJECT, properties={}),
    ),
    types.FunctionDeclaration(
        name="approve_well",
        description="Promote a single well to production. Pass the staging_id of the well to approve.",
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={"staging_id": types.Schema(
                type=types.Type.INTEGER,
                description="staging_id of the well to approve",
            )},
            required=["staging_id"],
        ),
    ),
    types.FunctionDeclaration(
        name="exclude_well",
        description="Exclude a single well from migration. Pass the staging_id of the well to exclude.",
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={"staging_id": types.Schema(
                type=types.Type.INTEGER,
                description="staging_id of the well to exclude",
            )},
            required=["staging_id"],
        ),
    ),
    types.FunctionDeclaration(
        name="approve_all_wells",
        description="Approve all pending wells for this scientist. Use when they confirm all data is valid.",
        parameters=types.Schema(type=types.Type.OBJECT, properties={}),
    ),
    types.FunctionDeclaration(
        name="exclude_all_wells",
        description="Exclude all pending wells for this scientist. Use when all readings are invalid or they object to migration.",
        parameters=types.Schema(type=types.Type.OBJECT, properties={}),
    ),
])


# ── Agent loop ────────────────────────────────────────────────────────────────

async def run_review_agent(
    trace_id:     str,
    scientist_name: str,
    admin_message:  str,
    approved_by:  str = "Review Resolution Agent",
) -> dict:
    """
    Run the Review Resolution Agent for a specific scientist.
    Returns agent's summary text + list of actions taken.
    """
    pool = await get_gds_pool()
    tid  = uuid_mod.UUID(trace_id)
    config = await load_promotion_config(pool, tid)

    client = get_client()
    actions_taken: list[dict] = []
    trace: list[dict] = []

    def _step(action: str, **kwargs):
        entry = {"action": action, "timestamp": datetime.utcnow().isoformat(),
                 **{k: str(v)[:300] for k, v in kwargs.items()}}
        trace.append(entry)
        logger.info("REVIEW_AGENT [%s] %s", action, str(kwargs)[:120])

    initial_msg = (
        f"Scientist: {scientist_name}\n"
        f"Migration trace: {trace_id}\n\n"
        f"Admin's report of what the scientist communicated:\n\"{admin_message}\"\n\n"
        f"Resolve this scientist's pending wells based on what they communicated."
    )

    contents: list[types.Content] = [
        types.Content(role="user", parts=[types.Part(text=initial_msg)])
    ]

    _cfg = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        tools=[TOOL_DEFINITIONS],
        temperature=0.1,
    )

    final_text = ""
    turns_used = 0

    for turn in range(MAX_TURNS):
        turns_used = turn + 1
        response   = await generate_with_backoff(client, contents=contents, config=_cfg, model=_MODEL)

        if not response.candidates:
            _step("NO_CANDIDATES")
            break

        candidate = response.candidates[0]
        if not candidate.content or not candidate.content.parts:
            _step("EMPTY_CONTENT")
            break

        contents.append(types.Content(role="model", parts=candidate.content.parts))
        tool_calls = [p for p in candidate.content.parts if p.function_call]

        if not tool_calls:
            final_text = " ".join(p.text for p in candidate.content.parts if p.text).strip()
            _step("AGENT_DONE", message=final_text[:200])
            break

        tool_results = []
        for part in tool_calls:
            fn   = part.function_call
            name = fn.name
            args = dict(fn.args) if fn.args else {}

            _step(f"TOOL_CALL:{name}", args=str(args)[:200])

            try:
                if name == "get_scientist_wells":
                    result = await _tool_get_scientist_wells(pool, tid, scientist_name, config)

                elif name == "approve_well":
                    sid    = int(args["staging_id"])
                    result = await _tool_approve_well(pool, sid, tid, config, approved_by)
                    if result["status"] == "success":
                        actions_taken.append({"action": "approved", "staging_id": sid,
                                              "well_position": result.get("well_position", "?")})
                        await _log_review_audit(pool, tid, "hitl_approved", approved_by, promoted=1)

                elif name == "exclude_well":
                    sid    = int(args["staging_id"])
                    result = await _tool_exclude_well(pool, sid, config)
                    if result["status"] == "success":
                        actions_taken.append({"action": "excluded", "staging_id": sid})
                        await _log_review_audit(pool, tid, "hitl_excluded", approved_by, excluded=1)

                elif name == "approve_all_wells":
                    result = await _tool_approve_all_wells(
                        pool, tid, scientist_name, config, approved_by)
                    if result["status"] == "success":
                        for wp in result.get("approved_wells", []):
                            actions_taken.append({"action": "approved", "well_position": wp})
                        n = result.get("approved_count", 0)
                        if n:
                            await _log_review_audit(pool, tid, "hitl_approved", approved_by, promoted=n)

                elif name == "exclude_all_wells":
                    result = await _tool_exclude_all_wells(pool, tid, scientist_name, config)
                    if result["status"] == "success":
                        actions_taken.append({"action": "excluded_all",
                                              "excluded_count": result.get("excluded_count", 0)})
                        n = result.get("excluded_count", 0)
                        if n:
                            await _log_review_audit(pool, tid, "hitl_excluded", approved_by, excluded=n)

                else:
                    result = {"status": "error", "error": f"Unknown tool: {name}"}

            except Exception as exc:
                result = {"status": "error", "error": f"{type(exc).__name__}: {exc}",
                          "traceback": traceback.format_exc()[:400]}

            _step(f"TOOL_RESULT:{name}",
                  status=result.get("status", ""), summary=str(result)[:200])

            tool_results.append(types.Part(
                function_response=types.FunctionResponse(name=name, response=result)
            ))

        contents.append(types.Content(role="user", parts=tool_results))

    return {
        "trace_id":       trace_id,
        "scientist_name": scientist_name,
        "result":         final_text,
        "actions_taken":  actions_taken,
        "turns_used":     turns_used,
        "reasoning_trace": trace,
    }


# ── Batch agent ───────────────────────────────────────────────────────────────

BATCH_SYSTEM_PROMPT = """You are the Review Resolution Agent for a scientific data migration pipeline.

You see ALL flagged scientists and their wells at once. The admin types one plain-English instruction
describing what needs to happen across any number of scientists.

STEP 1 — Always call get_all_pending_wells first to see the full picture.

STEP 2 — Interpret the admin's instruction. Examples:
  "Remove Chen_L's wells"                     → exclude_all_wells for Chen_L
  "Singh_A and Gupta_P don't want their data" → exclude_all_wells for Singh_A, then Gupta_P
  "Approve everyone except Lee_H"             → approve_all_wells for every scientist except Lee_H; exclude_all_wells for Lee_H
  "Remove B03 from Chen_L"                    → exclude_well for Chen_L's B03 staging_id
  "Approve all"                               → approve_all_wells for every scientist with pending wells
  "Remove all flagged data"                   → exclude_all_wells for every scientist

STEP 3 — Act. Use approve_all_wells / exclude_all_wells for whole-scientist actions.
Use approve_well / exclude_well for specific well positions (pass the staging_id).
scientist_name must match exactly what get_all_pending_wells returned.

STEP 4 — After acting, call get_all_pending_wells again to confirm the final state.

STEP 5 — Write a clean summary:
RESOLUTION SUMMARY
Actions taken:
• [Scientist name]: [what was done] ([well count] wells)
• ...
Total approved: X  |  Total excluded: Y
"""

BATCH_TOOL_DEFINITIONS = types.Tool(function_declarations=[
    types.FunctionDeclaration(
        name="get_all_pending_wells",
        description="Load all flagged scientists and their wells for this migration run. Always call this first.",
        parameters=types.Schema(type=types.Type.OBJECT, properties={}),
    ),
    types.FunctionDeclaration(
        name="approve_all_wells",
        description="Approve all pending wells for a scientist.",
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={"scientist_name": types.Schema(type=types.Type.STRING)},
            required=["scientist_name"],
        ),
    ),
    types.FunctionDeclaration(
        name="exclude_all_wells",
        description="Exclude all pending wells for a scientist.",
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={"scientist_name": types.Schema(type=types.Type.STRING)},
            required=["scientist_name"],
        ),
    ),
    types.FunctionDeclaration(
        name="approve_well",
        description="Approve a single well by staging_id.",
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={"staging_id": types.Schema(type=types.Type.INTEGER)},
            required=["staging_id"],
        ),
    ),
    types.FunctionDeclaration(
        name="exclude_well",
        description="Exclude a single well by staging_id.",
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={"staging_id": types.Schema(type=types.Type.INTEGER)},
            required=["staging_id"],
        ),
    ),
])


async def run_batch_review_agent(
    trace_id:    str,
    message:     str,
    approved_by: str = "Review Resolution Agent",
) -> dict:
    """
    One agent, one instruction, acts across ALL flagged scientists at once.
    Admin types plain English — the agent figures out who to approve/exclude.
    """
    pool   = await get_gds_pool()
    tid    = uuid_mod.UUID(trace_id)
    config = await load_promotion_config(pool, tid)
    client = get_client()

    actions_taken: list[dict] = []

    contents: list[types.Content] = [
        types.Content(role="user", parts=[types.Part(text=(
            f"Migration trace: {trace_id}\n\n"
            f"Admin instruction:\n\"{message}\"\n\n"
            f"Start by loading all pending wells, then act on the instruction."
        ))])
    ]

    _cfg = types.GenerateContentConfig(
        system_instruction=BATCH_SYSTEM_PROMPT,
        tools=[BATCH_TOOL_DEFINITIONS],
        temperature=0.1,
    )

    final_text = ""

    for _ in range(MAX_TURNS):
        response = await generate_with_backoff(client, contents=contents, config=_cfg, model=_MODEL)

        if not response.candidates:
            break

        candidate = response.candidates[0]
        if not candidate.content or not candidate.content.parts:
            break

        contents.append(types.Content(role="model", parts=candidate.content.parts))
        tool_calls = [p for p in candidate.content.parts if p.function_call]

        if not tool_calls:
            final_text = " ".join(p.text for p in candidate.content.parts if p.text).strip()
            break

        tool_results = []
        for part in tool_calls:
            fn, args = part.function_call, dict(part.function_call.args or {})
            name = fn.name

            try:
                if name == "get_all_pending_wells":
                    result = await _tool_get_all_pending_wells(pool, tid, config)

                elif name == "approve_all_wells":
                    sname  = args["scientist_name"]
                    result = await _tool_approve_all_wells(pool, tid, sname, config, approved_by)
                    if result["status"] == "success":
                        n = result.get("approved_count", 0)
                        actions_taken.append({"scientist": sname, "action": "approved_all", "count": n})
                        if n:
                            await _log_review_audit(pool, tid, "hitl_approved", approved_by, promoted=n)

                elif name == "exclude_all_wells":
                    sname  = args["scientist_name"]
                    result = await _tool_exclude_all_wells(pool, tid, sname, config)
                    if result["status"] == "success":
                        n = result.get("excluded_count", 0)
                        actions_taken.append({"scientist": sname, "action": "excluded_all", "count": n})
                        if n:
                            await _log_review_audit(pool, tid, "hitl_excluded", approved_by, excluded=n)

                elif name == "approve_well":
                    sid    = int(args["staging_id"])
                    result = await _tool_approve_well(pool, sid, tid, config, approved_by)
                    if result["status"] == "success":
                        actions_taken.append({"action": "approved", "staging_id": sid,
                                              "well_position": result.get("well_position")})
                        await _log_review_audit(pool, tid, "hitl_approved", approved_by, promoted=1)

                elif name == "exclude_well":
                    sid    = int(args["staging_id"])
                    result = await _tool_exclude_well(pool, sid, config)
                    if result["status"] == "success":
                        actions_taken.append({"action": "excluded", "staging_id": sid})
                        await _log_review_audit(pool, tid, "hitl_excluded", approved_by, excluded=1)

                else:
                    result = {"status": "error", "error": f"Unknown tool: {name}"}

            except Exception as exc:
                result = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}

            tool_results.append(types.Part(function_response=types.FunctionResponse(
                name=name, response=result)))

        contents.append(types.Content(role="user", parts=tool_results))

    return {
        "trace_id":     trace_id,
        "result":       final_text,
        "actions_taken": actions_taken,
    }
