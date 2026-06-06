import uuid
import json
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
    """All GDS scientists with EC50, R², and curve quality per compound."""
    pool = await get_gds_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT u.gds_user_id,
                   u.name,
                   u.role,
                   COUNT(e.experiment_id)  AS experiment_count,
                   AVG(e.ec50_um)          AS avg_ec50,
                   AVG(e.r_squared)        AS avg_r_squared,
                   MAX(e.approved_at)      AS last_import
            FROM   gds_users u
            LEFT JOIN gds_experiments e ON e.gds_user_id = u.gds_user_id
            GROUP  BY u.gds_user_id, u.name, u.role
            ORDER  BY u.name
        """)
    return JSONResponse(content={"users": [_s(dict(r)) for r in rows]})


@router.get("/users/{gds_user_id}/experiments")
async def get_user_experiments(gds_user_id: str):
    """Compound experiment records for a single GDS scientist, with curve data."""
    pool = await get_gds_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT experiment_id, trace_id, compound_id, assay_type,
                   ec50_um, hill_slope, r_squared, curve_quality,
                   num_concentration_points, signal,
                   plate_barcode, curve_data,
                   neg_ctrl_mean, pos_ctrl_mean,
                   approved_at, approved_by, source_experiment_id
            FROM   gds_experiments
            WHERE  gds_user_id = $1
            ORDER  BY compound_id
        """, uuid.UUID(gds_user_id))

    def _exp(r: dict) -> dict:
        out = _s(r)
        cd = out.get("curve_data")
        if isinstance(cd, str):
            try:
                out["curve_data"] = json.loads(cd)
            except Exception:
                out["curve_data"] = []
        elif cd is None:
            out["curve_data"] = []
        return out

    return JSONResponse(content={
        "gds_user_id": gds_user_id,
        "experiments": [_exp(dict(r)) for r in rows],
        "total":       len(rows),
    })


@router.get("/plates/{plate_barcode}")
async def get_plate(plate_barcode: str):
    """All wells on a plate from all scientists' curve_data — for the plate heatmap."""
    pool = await get_gds_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT u.name AS scientist_name,
                   e.compound_id,
                   e.ec50_um,
                   e.r_squared,
                   e.curve_quality,
                   e.curve_data,
                   e.neg_ctrl_mean,
                   e.pos_ctrl_mean
            FROM   gds_experiments e
            JOIN   gds_users u ON u.gds_user_id = e.gds_user_id
            WHERE  e.plate_barcode = $1
            ORDER  BY u.name
        """, plate_barcode)

    wells: list[dict] = []
    for r in rows:
        raw_cd = r["curve_data"] or []
        curve_data = json.loads(raw_cd) if isinstance(raw_cd, str) else raw_cd
        for pt in curve_data:
            wells.append({
                "scientist_name": r["scientist_name"],
                "compound_id":    r["compound_id"],
                "well_position":  pt.get("well_position"),
                "conc_um":        pt.get("conc_um"),
                "response":       pt.get("response"),
                "quality":        pt.get("quality", "valid"),
            })

    return JSONResponse(content={
        "plate_barcode": plate_barcode,
        "wells":         wells,
        "scientists":    [_s(dict(r)) for r in rows],
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
