"""
Shared pipeline helpers — imported by both the deterministic pipeline (agent.py)
and the orchestrator agent (orchestrator_agent.py) to avoid circular imports.
"""

import uuid
import json
import logging
from collections import defaultdict
from uuid import UUID

import numpy as np

from app.connectors import get_gds_pool, get_abase_pool
from app.core.mapping import validate_identifier as _validate_identifier

logger = logging.getLogger(__name__)


def _scientist_name(row: dict) -> str:
    return (row.get("scientist_name") or row.get("name")
            or row.get("scientist") or row.get("user_name") or "")


async def write_to_staging(trace_id: str, records: list[dict], promotion_config: dict) -> int:
    if not records:
        return 0

    staging_table = promotion_config.get("staging_table")
    if not staging_table:
        raise ValueError(
            "promotion_config must include 'staging_table'. "
            "Agent must discover and specify the target staging table."
        )
    insert_sql = f"""
        INSERT INTO {staging_table} (trace_id, data, risk_level, status)
        VALUES ($1, $2, $3, 'pending')
        ON CONFLICT DO NOTHING
    """

    def _safe_json(v):
        if isinstance(v, UUID):        return str(v)
        if hasattr(v, "isoformat"):    return v.isoformat()
        if isinstance(v, np.integer):  return int(v)
        if isinstance(v, np.floating): return float(v)
        if isinstance(v, np.ndarray):  return v.tolist()
        return v

    def _payload(r: dict) -> dict:
        src = r["data"] if isinstance(r.get("data"), dict) else r
        return {k: _safe_json(v) for k, v in src.items()
                if k not in ("risk_level", "trace_id", "data")}

    pool = await get_gds_pool()
    tid  = uuid.UUID(trace_id)
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.executemany(
                insert_sql,
                [(tid, json.dumps(_payload(r)), r.get("risk_level", "auto")) for r in records],
            )
    return len(records)


async def store_migration_plan(trace_id: str, promotion_config: dict, initiated_by: str = "System") -> None:
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


async def mark_source_migrating(records: list[dict]) -> int:
    names = sorted({_scientist_name(r) for r in records} - {""})
    if not names:
        return 0
    abase_pool = await get_abase_pool()
    async with abase_pool.acquire() as aconn:
        result = await aconn.execute(
            "UPDATE users SET migration_status='migrating' WHERE name = ANY($1::text[])", names)
    return int(result.split()[-1])


async def force_all_to_review(conn, trace_id: str, staging_table: str) -> int:
    table  = _validate_identifier(staging_table, "staging_table")
    result = await conn.execute(f"""
        UPDATE {table}
        SET    risk_level = 'review'
        WHERE  trace_id = $1 AND status = 'pending' AND risk_level = 'auto'
    """, uuid.UUID(trace_id))
    return int(result.split()[-1])


async def purge_committed_sources(trace_id: str, staging_table: str) -> tuple[int, list]:
    table    = _validate_identifier(staging_table, "staging_table")
    gds_pool = await get_gds_pool()
    async with gds_pool.acquire() as conn:
        rows = await conn.fetch(f"""
            SELECT COALESCE(data->>'scientist_name', data->>'name',
                            data->>'scientist', data->>'user_name') AS sname,
                   status
            FROM   {table} WHERE trace_id = $1
        """, uuid.UUID(trace_id))

    by_scientist: dict = defaultdict(set)
    for r in rows:
        if r["sname"]:
            by_scientist[r["sname"]].add(r["status"])

    committed = [s for s, statuses in by_scientist.items() if statuses == {"auto_approved"}]
    if not committed:
        return 0, []

    abase_pool = await get_abase_pool()
    async with abase_pool.acquire() as aconn:
        result = await aconn.execute(
            "DELETE FROM users WHERE name = ANY($1::text[])", committed)
    return int(result.split()[-1]), committed
