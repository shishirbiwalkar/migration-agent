import uuid as uuid_mod
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.connectors import get_abase_pool, get_gds_pool
from app.agents.review_agent import (
    run_review_agent,
    load_promotion_config,
    _tool_get_scientist_wells,
)

router = APIRouter(prefix="/api/reviewer", tags=["reviewer"])
logger = logging.getLogger(__name__)


# ── GET /api/reviewer/{trace_id}/{scientist_name} ────────────────────────────

@router.get("/{trace_id}/{scientist_name}")
async def get_scientist_context(trace_id: str, scientist_name: str):
    """Load a scientist's current staging picture for the reviewer UI."""
    pool = await get_gds_pool()
    tid  = uuid_mod.UUID(trace_id)

    config = await load_promotion_config(pool, tid)
    result = await _tool_get_scientist_wells(pool, tid, scientist_name, config)

    if result.get("status") == "not_found":
        raise HTTPException(status_code=404, detail=result["error"])

    return JSONResponse(content=result)


# ── POST /api/reviewer/{trace_id}/{scientist_name}/run ───────────────────────

class RunRequest(BaseModel):
    message:     str
    approved_by: str = "Review Resolution Agent"


@router.post("/{trace_id}/{scientist_name}/run")
async def run_reviewer(trace_id: str, scientist_name: str, body: RunRequest):
    """
    Run the Review Resolution Agent for a specific scientist.
    Admin provides a natural language description of what the scientist communicated.
    Agent reasons about context and resolves their pending wells.
    """
    log = logging.LoggerAdapter(logger, {"trace_id": trace_id})
    log.info("Review agent started: scientist=%s", scientist_name)

    result = await run_review_agent(
        trace_id=trace_id,
        scientist_name=scientist_name,
        admin_message=body.message,
        approved_by=body.approved_by,
    )

    # Post-run: if all wells are resolved (none pending), clean up ABASE
    abase_deleted = 0
    try:
        pool = await get_gds_pool()
        tid  = uuid_mod.UUID(trace_id)
        config = await load_promotion_config(pool, tid)
        staging_tbl = config.get("staging_table", "gds_staging_experiments")

        _NAME_FILTER = """(
            data->>'scientist_name' = $2
            OR data->>'name' = $2
            OR data->>'scientist' = $2
            OR data->>'user_name' = $2
        )"""

        async with pool.acquire() as conn:
            statuses = await conn.fetch(f"""
                SELECT status FROM {staging_tbl}
                WHERE trace_id=$1 AND {_NAME_FILTER}
            """, tid, scientist_name)

        status_set = {r["status"] for r in statuses}
        all_resolved = "pending" not in status_set
        none_excluded = "excluded" not in status_set

        if all_resolved and none_excluded and statuses:
            abase_pool = await get_abase_pool()
            async with abase_pool.acquire() as aconn:
                res = await aconn.execute(
                    "DELETE FROM users WHERE name = $1", scientist_name)
                abase_deleted = int(res.split()[-1])
                if abase_deleted:
                    log.info("Deleted scientist %s from ABASE after full approval", scientist_name)

    except Exception as e:
        log.warning("ABASE cleanup skipped: %s", e)

    result["abase_deleted"] = abase_deleted
    return JSONResponse(content=result)
