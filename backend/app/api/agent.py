import os
import uuid
import json
import logging
import numpy as np
from uuid import UUID

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.agents import run_migration_agent
from app.connectors import get_gds_pool, get_abase_pool
from app.api.migration import auto_approve_clean_rows

router = APIRouter(prefix="/api/agent", tags=["agent"])
logger = logging.getLogger(__name__)


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

    pool = await get_gds_pool()
    tid  = uuid.UUID(trace_id)
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.executemany(
                insert_sql,
                [
                    (
                        tid,
                        json.dumps({k: _safe_json(v)
                                    for k, v in r.items() if k != "risk_level"}),
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


@router.post("/run")
async def run_agent(body: AgentRunRequest = AgentRunRequest()):
    """
    Trigger the AI migration agent.

    Agent is READ-ONLY — it discovers schemas, transforms data in-memory,
    classifies rows statistically, and returns a promotion config.
    This endpoint then handles all DB writes:
      1. Write cleaned records → GDS staging
      2. Store promotion config → migration_plans table
      3. Auto-promote 'auto' rows → GDS production (no human needed)
      4. 'review' rows stay in staging for human approval at /review

    Pass source_db_url / target_db_url to migrate any two PostgreSQL databases.
    Defaults to ABASE → GDS from environment variables.
    """
    trace_id = str(uuid.uuid4())
    log = logging.LoggerAdapter(logger, {"trace_id": trace_id})
    log.info("Agent run started (source=%s, target=%s)",
             "custom" if body.source_db_url else "ABASE_env",
             "custom" if body.target_db_url else "GDS_env")

    # ── Step 1: Run agent (READ-ONLY) ─────────────────────────────────────────
    try:
        agent_result = await run_migration_agent(
            trace_id   = trace_id,
            source_url = body.source_db_url or os.environ.get("ABASE_DATABASE_URL"),
            target_url = body.target_db_url or os.environ.get("GDS_DATABASE_URL"),
        )
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        log.exception("Agent failed")
        raise HTTPException(status_code=500, detail=f"Agent error: {e}")

    cleaned_records  = agent_result.pop("cleaned_records")   # remove from response
    promotion_config = agent_result["promotion_config"]

    # ── Step 2: Write to staging (infrastructure, not agent) ─────────────────
    staged = await _write_to_staging(trace_id, cleaned_records, promotion_config)
    log.info("Wrote %d records to staging", staged)

    # ── Step 2b: Remove fully auto-approved scientists from ABASE ────────────
    # Only remove scientists whose ALL wells were auto-approved (no HITL needed).
    # Scientists with any review wells stay in ABASE until HITL is resolved.
    try:
        from collections import defaultdict
        scientist_risks: dict = defaultdict(set)
        for r in cleaned_records:
            name = r.get("scientist_name") or r.get("name") or r.get("scientist") or r.get("user_name") or ""
            if name:
                scientist_risks[name].add(r.get("risk_level", "auto"))

        fully_auto = [name for name, risks in scientist_risks.items() if "review" not in risks]
        if fully_auto:
            abase_pool = await get_abase_pool()
            async with abase_pool.acquire() as aconn:
                result = await aconn.execute(
                    "DELETE FROM users WHERE name = ANY($1::text[])", fully_auto
                )
                log.info("Removed %d fully auto-approved scientists from ABASE: %s",
                         int(result.split()[-1]), fully_auto)
        log.info("Scientists with HITL review wells remain in ABASE: %s",
                 [n for n, r in scientist_risks.items() if "review" in r])
    except Exception as e:
        log.warning("ABASE scientist removal skipped: %s", e)

    # ── Step 3: Store promotion config ────────────────────────────────────────
    if not promotion_config:
        raise HTTPException(status_code=500,
            detail="Agent returned empty promotion_config — cannot promote or approve rows.")
    await _store_migration_plan(trace_id, promotion_config, body.initiated_by)
    log.info("Promotion config stored")

    # ── Step 3b: Mapping Critic — audit the mapping before promotion ──────────
    # Proposer-critic pattern. Best-effort: a critic failure never blocks the
    # migration. For the trusted ABASE→GDS path we proceed regardless; the verdict
    # is surfaced for review (Gate 1). An unknown schema would gate on FLAG.
    mapping_review = None
    try:
        from app.agents.critic_agent import run_critic_agent
        mapping_review = await run_critic_agent(trace_id)
        log.info("Mapping critic verdict: %s", mapping_review.get("verdict"))
    except Exception as e:
        log.warning("Mapping critic skipped: %s", e)

    # ── Step 4: Auto-promote clean rows (partial HITL) ────────────────────────
    partial = await auto_approve_clean_rows(trace_id, approved_by="AI Agent (auto)")

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

    log.info("Done: %d staged, %d auto-approved, %d pending review, verdict=%s",
             staged, partial["auto_promoted"],
             partial["pending_review"], agent_result["quality_verdict"])

    return JSONResponse(content=agent_result)
