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
from app.api.pipeline_helpers import (
    write_to_staging      as _write_to_staging,
    store_migration_plan  as _store_migration_plan,
    mark_source_migrating as _mark_source_migrating,
    force_all_to_review   as _force_all_to_review,
    purge_committed_sources as _purge_committed_sources,
)

router = APIRouter(prefix="/api/agent", tags=["agent"])
logger = logging.getLogger(__name__)

# Hold references to in-flight background jobs so the event loop doesn't GC them.
_background_tasks: set = set()


class AgentRunRequest(BaseModel):
    source_db_url: str | None = None  # default: ABASE_DATABASE_URL env var (any source database supported)
    target_db_url: str | None = None  # default: GDS_DATABASE_URL env var (any target database supported)
    initiated_by:  str        = "System"




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
    Entry point for both sync and async migration runs.
    Delegates to the Orchestrator Agent when ORCHESTRATOR_AGENT=true,
    otherwise runs the deterministic pipeline below.
    """
    if os.getenv("ORCHESTRATOR_AGENT", "false").lower() == "true":
        from app.agents.orchestrator_agent import run_orchestrator_agent
        return await run_orchestrator_agent(trace_id, source_url, target_url, initiated_by)

    log = logging.LoggerAdapter(logger, {"trace_id": trace_id})

    # ── Step 0: Source backup — infrastructure code, non-blocking ────────────
    # Backup is enterprise infrastructure, not agent logic. Migration proceeds
    # even if backup is unavailable (BACKUP_PROVIDER=none or table not yet created).
    try:
        from app.core.backup import trigger_backup
        from app.core.backup_store import save_backup
        import urllib.parse as _urlparse
        backup = await trigger_backup(source_url, trace_id)
        parsed = _urlparse.urlparse(source_url)
        source_host = parsed.hostname or source_url.split("@")[-1].split("/")[0]
        await save_backup(
            trace_id=trace_id, source_host=source_host,
            provider=backup["provider"], snapshot_id=backup.get("snapshot_id"),
            status=backup["status"], metadata=backup.get("metadata", {}),
        )
        log.info("Backup: provider=%s status=%s", backup["provider"], backup["status"])
    except Exception as e:
        log.warning("Backup skipped (non-blocking): %s", e)

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
        verdict  = (mapping_review or {}).get("verdict")
        findings = (mapping_review or {}).get("findings", [])
        # Only force all rows to HITL when there is at least one genuine error-severity
        # finding (type mismatch, data corruption risk). Warnings alone do not escalate —
        # they are surfaced in the UI but clean rows still auto-promote.
        has_errors = any(f.get("severity") == "error" for f in findings)
        critic_flagged = verdict == "FLAG" and has_errors
        log.info("Mapping critic verdict: %s (error_findings=%s)", verdict, has_errors)
    except Exception as e:
        log.warning("Mapping critic skipped: %s", e)

    # ── Step 3c: Enforce a FLAG — force all rows to mandatory review ──────────
    staging_table = promotion_config.get("staging_table")
    if not staging_table:
        raise RuntimeError(
            "promotion_config must include 'staging_table'. "
            "Agent must discover and specify the target staging table."
        )
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
