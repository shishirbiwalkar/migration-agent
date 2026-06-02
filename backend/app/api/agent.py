import os
import uuid
import json
import asyncio
import logging
import numpy as np
from uuid import UUID
from collections import defaultdict

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.agents import run_migration_agent, compute_schema_fingerprints, replay_mapping
from app.connectors import get_gds_pool, get_abase_pool
from app.api.migration import auto_approve_clean_rows
from app.core.mapping import validate_identifier as _validate_identifier

router = APIRouter(prefix="/api/agent", tags=["agent"])
logger = logging.getLogger(__name__)

# Hold references to in-flight background jobs so the event loop doesn't GC them.
_background_tasks: set = set()


class AgentRunRequest(BaseModel):
    source_db_url: str | None = None  # default: ABASE_DATABASE_URL env var
    target_db_url: str | None = None  # default: GDS_DATABASE_URL env var
    initiated_by:  str        = "System"


async def _write_to_staging(trace_id: str, records: list[dict], promotion_config: dict) -> int:
    """Write agent-produced records to staging as JSONB — works for any migration schema."""
    if not records:
        return 0

    staging_table = promotion_config.get("staging_table", "gds_staging_experiments")
    insert_sql    = f"""
        INSERT INTO {staging_table} (trace_id, data, risk_level, status)
        VALUES ($1, $2, $3, 'pending')
        ON CONFLICT DO NOTHING
    """

    def _safe_json(v):
        if isinstance(v, UUID):          return str(v)
        if hasattr(v, "isoformat"):      return v.isoformat()
        if isinstance(v, np.integer):    return int(v)
        if isinstance(v, np.floating):   return float(v)
        if isinstance(v, np.ndarray):    return v.tolist()
        return v

    def _payload(r: dict) -> dict:
        # Some agent transforms emit rows already shaped like the staging table itself
        # (a nested 'data' dict plus trace_id/risk_level columns). Unwrap so the JSONB
        # holds FLAT business fields — otherwise scientist_name/user_name end up buried
        # under data.data, unreachable, and HITL can't group by scientist (shows one
        # collective bucket instead of separate users).
        src = r["data"] if isinstance(r.get("data"), dict) else r
        return {k: _safe_json(v) for k, v in src.items()
                if k not in ("risk_level", "trace_id", "data")}

    pool = await get_gds_pool()
    tid  = uuid.UUID(trace_id)
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.executemany(
                insert_sql,
                [
                    (
                        tid,
                        json.dumps(_payload(r)),
                        r.get("risk_level", "auto"),
                    )
                    for r in records
                ],
            )
    return len(records)


async def _store_migration_plan(trace_id: str, promotion_config: dict, initiated_by: str = "System") -> None:
    """Persist the agent's promotion config for the approve endpoint to use."""
    pool = await get_gds_pool()
    tid  = uuid.UUID(trace_id)
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO migration_plans (trace_id, plan_json, initiated_by)
            VALUES ($1, $2, $3)
            ON CONFLICT (trace_id) DO UPDATE
                SET plan_json    = EXCLUDED.plan_json,
                    initiated_by = EXCLUDED.initiated_by
        """, tid, json.dumps(promotion_config), initiated_by)


def _scientist_name(row: dict) -> str:
    """Best-effort scientist name from a record/staging row (schema-agnostic)."""
    return (row.get("scientist_name") or row.get("name")
            or row.get("scientist") or row.get("user_name") or "")


async def _mark_source_migrating(records: list[dict]) -> int:
    """
    Soft-delete safety: mark involved source scientists as 'migrating' instead of
    deleting them. No source data is destroyed here. Best-effort — a source without
    a migration_status column (any generic non-ABASE DB) is silently skipped.
    """
    names = sorted({_scientist_name(r) for r in records} - {""})
    if not names:
        return 0
    abase_pool = await get_abase_pool()
    async with abase_pool.acquire() as aconn:
        result = await aconn.execute(
            "UPDATE users SET migration_status='migrating' WHERE name = ANY($1::text[])",
            names,
        )
    return int(result.split()[-1])


async def _force_all_to_review(conn, trace_id: str, staging_table: str) -> int:
    """
    Mapping-critic enforcement: flip every still-'auto' staged row to 'review' so it
    bypasses auto-promotion and lands in mandatory human review. After this, the
    auto-promote step finds zero 'auto' rows and promotes nothing.
    """
    table = _validate_identifier(staging_table, "staging_table")
    result = await conn.execute(f"""
        UPDATE {table}
        SET    risk_level = 'review'
        WHERE  trace_id = $1 AND status = 'pending' AND risk_level = 'auto'
    """, uuid.UUID(trace_id))
    return int(result.split()[-1])


async def _purge_committed_sources(trace_id: str, staging_table: str) -> tuple[int, list]:
    """
    Post-verification hard delete. Runs ONLY after auto-promotion has committed.
    A source scientist is deleted from ABASE only if EVERY one of their staged rows
    is 'auto_approved' — i.e. provably live in GDS production, with nothing pending,
    flagged, or excluded. This is the only place source data is ever destroyed in
    Stage 1, and only after the target transaction has committed.
    """
    table = _validate_identifier(staging_table, "staging_table")
    gds_pool = await get_gds_pool()
    async with gds_pool.acquire() as conn:
        rows = await conn.fetch(f"""
            SELECT COALESCE(data->>'scientist_name', data->>'name',
                            data->>'scientist', data->>'user_name') AS sname,
                   status
            FROM   {table}
            WHERE  trace_id = $1
        """, uuid.UUID(trace_id))

    by_scientist: dict = defaultdict(set)
    for r in rows:
        if r["sname"]:
            by_scientist[r["sname"]].add(r["status"])

    # Fully committed = every staged row reached production, none left behind.
    committed = [s for s, statuses in by_scientist.items() if statuses == {"auto_approved"}]
    if not committed:
        return 0, []

    abase_pool = await get_abase_pool()
    async with abase_pool.acquire() as aconn:
        result = await aconn.execute(
            "DELETE FROM users WHERE name = ANY($1::text[])", committed)
    return int(result.split()[-1]), committed


async def _lookup_cached_mapping(fingerprint: str) -> dict | None:
    """Return a cached mapping for this schema fingerprint, or None on a miss."""
    pool = await get_gds_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT transform_script, promotion_config
            FROM   migration_mappings
            WHERE  schema_fingerprint = $1
        """, fingerprint)
        if not row:
            return None
        await conn.execute("""
            UPDATE migration_mappings
            SET    hit_count = hit_count + 1, last_used_at = NOW()
            WHERE  schema_fingerprint = $1
        """, fingerprint)
    cfg = row["promotion_config"]
    return {
        "transform_script": row["transform_script"],
        "promotion_config": cfg if isinstance(cfg, dict) else json.loads(cfg),
    }


async def _store_cached_mapping(fingerprints: dict, transform_script: str,
                                promotion_config: dict, trace_id: str) -> None:
    """Persist a freshly-derived mapping so future runs of this schema pair can replay it."""
    pool = await get_gds_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO migration_mappings
                (schema_fingerprint, source_fingerprint, target_fingerprint,
                 transform_script, promotion_config, derived_trace_id, last_used_at)
            VALUES ($1, $2, $3, $4, $5, $6, NOW())
            ON CONFLICT (schema_fingerprint) DO UPDATE SET
                transform_script = EXCLUDED.transform_script,
                promotion_config = EXCLUDED.promotion_config,
                derived_trace_id = EXCLUDED.derived_trace_id,
                last_used_at     = NOW()
        """, fingerprints["combined"], fingerprints["source"], fingerprints["target"],
             transform_script, json.dumps(promotion_config), uuid.UUID(trace_id))


async def _create_job(trace_id: str, initiated_by: str) -> None:
    pool = await get_gds_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO migration_jobs (trace_id, status, initiated_by)
            VALUES ($1, 'queued', $2)
            ON CONFLICT (trace_id) DO NOTHING
        """, uuid.UUID(trace_id), initiated_by)


async def _update_job(trace_id: str, *, status: str, result: dict | None = None,
                      error: str | None = None, mapping_source: str | None = None) -> None:
    pool = await get_gds_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            UPDATE migration_jobs
            SET    status = $2,
                   result = COALESCE($3, result),
                   error  = $4,
                   mapping_source = COALESCE($5, mapping_source),
                   updated_at = NOW()
            WHERE  trace_id = $1
        """, uuid.UUID(trace_id), status,
             json.dumps(result) if result is not None else None,
             error, mapping_source)


def _resolve_urls(body: "AgentRunRequest") -> tuple[str, str]:
    return (
        body.source_db_url or os.environ.get("ABASE_DATABASE_URL"),
        body.target_db_url or os.environ.get("GDS_DATABASE_URL"),
    )


async def _execute_migration(trace_id: str, source_url: str, target_url: str,
                             initiated_by: str) -> dict:
    """
    The full pipeline, independent of how it was triggered (sync request or async job).
    Step 1 obtains the mapping — replaying a cached one when the schema pair is unchanged
    (no LLM), otherwise running the agent and caching the result. Steps 2–4b are the
    deterministic write/critic/promote pipeline and are identical for both paths.
    Raises RuntimeError on a fatal problem; callers decide how to surface it.
    """
    log = logging.LoggerAdapter(logger, {"trace_id": trace_id})

    # ── Step 1: Obtain the mapping (cache replay, or agent + cache store) ──────
    fp = await compute_schema_fingerprints(source_url, target_url)
    cached = await _lookup_cached_mapping(fp["combined"])
    if cached:
        log.info("Mapping cache HIT (fp=%s) — replaying deterministically, LLM skipped",
                 fp["combined"])
        agent_result = await replay_mapping(
            trace_id, source_url, cached["transform_script"], cached["promotion_config"])
    else:
        log.info("Mapping cache MISS (fp=%s) — running agent", fp["combined"])
        agent_result = await run_migration_agent(
            trace_id=trace_id, source_url=source_url, target_url=target_url)
        # Cache ONLY a complete mapping — both a transform script AND a non-empty
        # promotion_config. A partial/incomplete agent run (e.g. LLM truncated by quota)
        # must never poison the cache, or every later run would replay the broken mapping.
        if agent_result.get("transform_script") and agent_result.get("promotion_config"):
            await _store_cached_mapping(
                fp, agent_result["transform_script"], agent_result["promotion_config"], trace_id)
            log.info("Cached mapping for future replay (fp=%s)", fp["combined"])

    cleaned_records  = agent_result.pop("cleaned_records")
    promotion_config = agent_result["promotion_config"]
    if not promotion_config:
        raise RuntimeError("Empty promotion_config — cannot promote or approve rows.")

    # ── Step 2: Write to staging ──────────────────────────────────────────────
    staged = await _write_to_staging(trace_id, cleaned_records, promotion_config)
    log.info("Wrote %d records to staging (mapping_source=%s)",
             staged, agent_result.get("mapping_source"))

    # ── Step 2b: Mark source rows 'migrating' (NO deletion) ──────────────────
    try:
        marked = await _mark_source_migrating(cleaned_records)
        log.info("Marked %d source scientists as 'migrating' (no data deleted)", marked)
    except Exception as e:
        log.warning("Source state-tracking skipped (non-ABASE source?): %s", e)

    # ── Step 3: Store promotion config ────────────────────────────────────────
    await _store_migration_plan(trace_id, promotion_config, initiated_by)

    # ── Step 3b: Mapping Critic (FLAG verdict is enforced) ────────────────────
    mapping_review = None
    critic_flagged = False
    try:
        from app.agents.critic_agent import run_critic_agent
        mapping_review = await run_critic_agent(trace_id)
        verdict = (mapping_review or {}).get("verdict")
        critic_flagged = verdict == "FLAG"
        log.info("Mapping critic verdict: %s", verdict)
    except Exception as e:
        log.warning("Mapping critic skipped: %s", e)

    # ── Step 3c: Enforce a FLAG — force all rows to mandatory review ──────────
    staging_table = promotion_config.get("staging_table", "gds_staging_experiments")
    if critic_flagged:
        gds_pool = await get_gds_pool()
        async with gds_pool.acquire() as conn:
            forced = await _force_all_to_review(conn, trace_id, staging_table)
        log.warning("Mapping critic FLAGGED — auto-promote bypassed; %d rows forced to review",
                    forced)

    # ── Step 4: Auto-promote clean rows ───────────────────────────────────────
    partial = await auto_approve_clean_rows(trace_id, approved_by="AI Agent (auto)")

    # ── Step 4b: Post-commit hard delete of fully-migrated sources ────────────
    if not critic_flagged:
        try:
            _n, deleted = await _purge_committed_sources(trace_id, staging_table)
            log.info("Post-commit: removed %d fully-migrated scientists from ABASE: %s",
                     _n, deleted)
        except Exception as e:
            log.warning("Post-commit source purge skipped (non-ABASE source?): %s", e)

    agent_result["auto_approved"]  = partial["auto_promoted"]
    agent_result["pending_review"] = partial["pending_review"]
    agent_result["hitl_required"]  = partial["pending_review"] > 0
    agent_result["status"]         = (
        "pending_human_review" if partial["pending_review"] > 0 else "auto_approved"
    )
    agent_result["hitl_url"] = (
        f"/review?trace_id={trace_id}" if partial["pending_review"] > 0 else None
    )
    agent_result["mapping_review"] = mapping_review
    agent_result["critic_flagged"] = critic_flagged

    log.info("Done: %d staged, %d auto-approved, %d pending review, source=%s, verdict=%s",
             staged, partial["auto_promoted"], partial["pending_review"],
             agent_result.get("mapping_source"), agent_result["quality_verdict"])
    return agent_result


@router.post("/run")
async def run_agent(body: AgentRunRequest = AgentRunRequest()):
    """
    Trigger a migration synchronously and return the full result (backward-compatible).

    Reuses a cached mapping for an unchanged schema pair (no LLM); otherwise runs the
    read-only agent and caches the learned mapping. For long-running migrations prefer
    POST /run/async (returns immediately, poll GET /jobs/{trace_id}).
    """
    trace_id = str(uuid.uuid4())
    log = logging.LoggerAdapter(logger, {"trace_id": trace_id})
    source_url, target_url = _resolve_urls(body)
    log.info("Agent run started (source=%s, target=%s)",
             "custom" if body.source_db_url else "ABASE_env",
             "custom" if body.target_db_url else "GDS_env")
    try:
        agent_result = await _execute_migration(trace_id, source_url, target_url, body.initiated_by)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        log.exception("Agent failed")
        raise HTTPException(status_code=500, detail=f"Agent error: {e}")
    return JSONResponse(content=agent_result)


async def _run_job(trace_id: str, source_url: str, target_url: str, initiated_by: str) -> None:
    """Background worker — runs the pipeline and records status/result in migration_jobs."""
    log = logging.LoggerAdapter(logger, {"trace_id": trace_id})
    try:
        await _update_job(trace_id, status="running")
        result = await _execute_migration(trace_id, source_url, target_url, initiated_by)
        await _update_job(trace_id, status="succeeded", result=result,
                          mapping_source=result.get("mapping_source"))
        log.info("Async job succeeded")
    except Exception as e:
        log.exception("Async job failed")
        await _update_job(trace_id, status="failed", error=str(e))


@router.post("/run/async", status_code=202)
async def run_agent_async(body: AgentRunRequest = AgentRunRequest()):
    """
    Trigger a migration in the background. Returns immediately with a trace_id;
    poll GET /api/agent/jobs/{trace_id} for status and the final result.
    """
    trace_id = str(uuid.uuid4())
    source_url, target_url = _resolve_urls(body)
    await _create_job(trace_id, body.initiated_by)
    task = asyncio.create_task(_run_job(trace_id, source_url, target_url, body.initiated_by))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return JSONResponse(status_code=202, content={
        "trace_id":   trace_id,
        "status":     "queued",
        "status_url": f"/api/agent/jobs/{trace_id}",
    })


@router.get("/jobs/{trace_id}")
async def get_job(trace_id: str):
    """Status (and result, once finished) of an async migration job."""
    pool = await get_gds_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT trace_id, status, mapping_source, result, error, initiated_by,
                   created_at, updated_at
            FROM   migration_jobs WHERE trace_id = $1
        """, uuid.UUID(trace_id))
    if not row:
        raise HTTPException(status_code=404, detail=f"No job for trace_id={trace_id}")
    d = dict(row)
    d["trace_id"]   = str(d["trace_id"])
    d["created_at"] = d["created_at"].isoformat()
    d["updated_at"] = d["updated_at"].isoformat()
    if d.get("result") and not isinstance(d["result"], dict):
        d["result"] = json.loads(d["result"])
    return JSONResponse(content=d)
