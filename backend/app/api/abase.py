import logging
import uuid
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from app.connectors import get_abase_pool, get_gds_pool

router = APIRouter(prefix="/api/abase", tags=["abase"])
logger = logging.getLogger(__name__)


def _serialize(row: dict) -> dict:
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
async def list_users():
    """All ABASE users — name, department, last_login, active_time_minutes."""
    pool = await get_abase_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT id, name, department, email,
                   last_login, active_time_minutes, created_at, migrated_at
            FROM   users
            ORDER  BY name
        """)
    users = [_serialize(dict(r)) for r in rows]
    return JSONResponse(content={"users": users, "total": len(users)})


@router.get("/users/{user_id}")
async def get_user(user_id: int):
    """Single ABASE user with only their non-migrated experiment wells."""
    abase_pool = await get_abase_pool()
    async with abase_pool.acquire() as conn:
        user = await conn.fetchrow("""
            SELECT id, name, department, email,
                   last_login, active_time_minutes, created_at, migrated_at
            FROM   users
            WHERE  id = $1
        """, user_id)

        if not user:
            raise HTTPException(status_code=404, detail=f"User {user_id} not found.")

        experiments = await conn.fetch("""
            SELECT id, plate_barcode, well_position, raw_value, recorded_at,
                   compound_id, concentration_um, assay_type
            FROM   experiments
            WHERE  user_id = $1
            ORDER  BY recorded_at
        """, user_id)

    # Filter out wells already promoted to GDS production
    all_experiments = [_serialize(dict(e)) for e in experiments]
    try:
        gds_pool = await get_gds_pool()
        async with gds_pool.acquire() as conn:
            migrated = await conn.fetch("""
                SELECT e.well_position
                FROM   gds_experiments e
                JOIN   gds_users u ON u.gds_user_id = e.gds_user_id
                WHERE  u.name = $1
            """, user["name"])
        migrated_positions = {r["well_position"] for r in migrated}
        remaining = [e for e in all_experiments
                     if e["well_position"] not in migrated_positions]
    except Exception:
        remaining = all_experiments

    return JSONResponse(content={
        "user":        _serialize(dict(user)),
        "experiments": remaining,
        "total":       len(remaining),
        "migrated":    len(all_experiments) - len(remaining),
    })


@router.get("/experiments")
async def list_experiments():
    """All ABASE experiments joined with scientist name — used by the AI agent."""
    pool = await get_abase_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT e.id,
                   u.name        AS scientist_name,
                   u.department,
                   e.plate_barcode,
                   e.well_position,
                   e.raw_value,
                   e.recorded_at
            FROM   experiments e
            JOIN   users u ON u.id = e.user_id
            ORDER  BY e.recorded_at
        """)
    records = [_serialize(dict(r)) for r in rows]
    return JSONResponse(content={"records": records, "total": len(records)})
