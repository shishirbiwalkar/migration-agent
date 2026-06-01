import logging
import uuid as uuid_mod

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from app.agents.critic_agent import run_critic_agent

router = APIRouter(prefix="/api/critic", tags=["critic"])
logger = logging.getLogger(__name__)


@router.post("/{trace_id}")
async def review_mapping(trace_id: str):
    """
    Mapping Critic — audit the proposed promotion_config for a trace.
    Returns a structured verdict (APPROVE / FLAG) with per-field findings.
    This is the proposer-critic check that backs Gate 1.
    """
    try:
        uuid_mod.UUID(trace_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid trace_id format.")

    result = await run_critic_agent(trace_id)
    if result.get("error"):
        raise HTTPException(status_code=404, detail=result["error"])

    return JSONResponse(content=result)
