import uuid
import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.connectors import get_gds_pool

router = APIRouter(prefix="/api/gds", tags=["gds"])
logger = logging.getLogger(__name__)


def _s(row: dict) -> dict:
    out = {}
    for k, v in row.items():
        if isinstance(v, uuid.UUID):
            out[k] = str(v)
        elif hasattr(v, "isoformat"):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out


@router.get("/users")
async def get_users():
    """All GDS users with their well-level experiment counts and avg signal."""
    pool = await get_gds_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT u.gds_user_id,
                   u.name,
                   u.role,
                   COUNT(e.experiment_id)  AS experiment_count,
                   AVG(e.signal)           AS avg_signal,
                   MAX(e.approved_at)      AS last_import
            FROM   gds_users u
            LEFT JOIN gds_experiments e ON e.gds_user_id = u.gds_user_id
            GROUP  BY u.gds_user_id, u.name, u.role
            ORDER  BY u.name
        """)
    return JSONResponse(content={"users": [_s(dict(r)) for r in rows]})


@router.get("/users/{gds_user_id}/experiments")
async def get_user_experiments(gds_user_id: str):
    """All well-level experiment records for a single GDS user."""
    pool = await get_gds_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT experiment_id, trace_id, well_position, signal, approved_at, approved_by,
                   source_experiment_id, compound_id, concentration, assay_type
            FROM   gds_experiments
            WHERE  gds_user_id = $1
            ORDER  BY well_position
        """, uuid.UUID(gds_user_id))
    return JSONResponse(content={
        "gds_user_id": gds_user_id,
        "experiments": [_s(dict(r)) for r in rows],
        "total":       len(rows),
    })


@router.get("/experiments")
async def get_experiments():
    """All GDS production experiment records with scientist info."""
    pool = await get_gds_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT e.experiment_id,
                   e.gds_user_id,
                   e.trace_id,
                   e.well_position,
                   e.signal,
                   e.approved_at,
                   e.approved_by,
                   u.name AS scientist_name,
                   u.role AS scientist_role
            FROM   gds_experiments e
            JOIN   gds_users u ON u.gds_user_id = e.gds_user_id
            ORDER  BY u.name, e.well_position
        """)

    experiments = [_s(dict(r)) for r in rows]
    avg_signal  = (
        round(sum(r["signal"] for r in experiments) / len(experiments), 4)
        if experiments else 0
    )

    return JSONResponse(content={
        "total":       len(experiments),
        "avg_signal":  avg_signal,
        "experiments": experiments,
    })
