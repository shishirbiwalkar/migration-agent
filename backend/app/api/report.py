import logging
import uuid as uuid_mod

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from app.connectors import get_gds_pool
from app.agents.verification_agent import run_verification_agent
from app.agents.report_agent import run_report_agent  # deterministic fallback

router = APIRouter(prefix="/api/report", tags=["report"])
logger = logging.getLogger(__name__)


@router.get("/completed")
async def get_completed_runs():
    """
    Returns all migration traces that are fully resolved (no pending review wells).
    Used by the dashboard to show completed runs with report links.
    """
    pool = await get_gds_pool()
    async with pool.acquire() as conn:
        # Traces that have staging rows but none pending
        rows = await conn.fetch("""
            SELECT
                trace_id,
                MIN(created_at)  AS run_date,
                COUNT(*)         AS total_rows,
                COUNT(*) FILTER (WHERE status = 'auto_approved') AS auto_approved,
                COUNT(*) FILTER (WHERE status = 'approved')      AS hitl_approved,
                COUNT(*) FILTER (WHERE status = 'excluded')      AS excluded,
                COUNT(*) FILTER (WHERE status = 'pending')       AS pending
            FROM gds_staging_experiments
            GROUP BY trace_id
            HAVING COUNT(*) FILTER (WHERE status = 'pending') = 0
            ORDER BY MIN(created_at) DESC
            LIMIT 20
        """)

    runs = []
    for r in rows:
        runs.append({
            "trace_id":     str(r["trace_id"]),
            "run_date":     r["run_date"].isoformat(),
            "total_rows":   int(r["total_rows"]),
            "auto_approved": int(r["auto_approved"]),
            "hitl_approved": int(r["hitl_approved"]),
            "excluded":     int(r["excluded"]),
            "in_production": int(r["auto_approved"]) + int(r["hitl_approved"]),
        })

    return JSONResponse(content={"runs": runs})


@router.post("/{trace_id}")
async def generate_report(trace_id: str):
    """Generate a verification report for a completed migration trace."""
    log = logging.LoggerAdapter(logger, {"trace_id": trace_id})
    log.info("Generating verification report")

    try:
        uuid_mod.UUID(trace_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid trace_id format.")

    # Agentic verifier first; fall back to the deterministic report script if the
    # agent fails to produce a report (e.g. Gemini quota/503).
    try:
        result = await run_verification_agent(trace_id)
        if result.get("error"):
            raise HTTPException(status_code=404, detail=result["error"])
        if result.get("report"):
            return JSONResponse(content=result)
        log.warning("Verification agent produced no report — falling back to script")
    except HTTPException:
        raise
    except Exception as e:
        log.warning("Verification agent failed (%s) — falling back to script", e)

    result = await run_report_agent(trace_id)
    if result.get("error"):
        raise HTTPException(status_code=404, detail=result["error"])
    return JSONResponse(content=result)
