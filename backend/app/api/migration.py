import uuid as uuid_mod
import logging

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import json as json_mod
from app.connectors import get_gds_pool, get_abase_pool
from app.core.mapping import (
    validate_identifier as _validate_identifier,
    coerce as _coerce,
    load_promotion_config as _load_promotion_config,
    NAME_FIELDS, WELL_FIELDS, SIGNAL_FIELDS, ROLE_FIELDS, pick_field,
)

router = APIRouter(prefix="/api/migrate", tags=["migration"])
logger = logging.getLogger(__name__)


def _staging_table(config: dict) -> str:
    return config.get("staging_table", "gds_staging_experiments")


async def _resolve_conflict_target(conn, table: str, proposed: list[str],
                                   available_cols: list[str]) -> list[str]:
    """
    The agent proposes ON CONFLICT columns, but they only work if they match a real
    UNIQUE/PK constraint. Validate the proposal against the table's actual constraints;
    if it doesn't match one, fall back to a real UNIQUE constraint whose columns are all
    present in the row being inserted. Deterministic infra guard — the agent proposes,
    infra guarantees correctness.
    """
    rows = await conn.fetch("""
        SELECT tc.constraint_type AS ctype,
               array_agg(kcu.column_name ORDER BY kcu.ordinal_position) AS cols
        FROM   information_schema.table_constraints tc
        JOIN   information_schema.key_column_usage kcu
            ON tc.constraint_name = kcu.constraint_name
           AND tc.table_schema    = kcu.table_schema
        WHERE  tc.table_name = $1 AND tc.table_schema = 'public'
           AND tc.constraint_type IN ('UNIQUE', 'PRIMARY KEY')
        GROUP  BY tc.constraint_name, tc.constraint_type
    """, table)
    constraints = [(r["ctype"], list(r["cols"])) for r in rows]

    # 1. Proposed keys exactly match a real constraint → use them as-is.
    for _ctype, cols in constraints:
        if set(cols) == set(proposed):
            return proposed

    # 2. Fall back: prefer a UNIQUE constraint fully covered by the row's columns
    #    (a surrogate PK like a serial would never conflict, so skip PK-only).
    avail = set(available_cols)
    unique_first = sorted(constraints, key=lambda c: 0 if c[0] == "UNIQUE" else 1)
    for ctype, cols in unique_first:
        if set(cols) <= avail:
            logger.warning(
                "Promotion: proposed ON CONFLICT %s has no matching constraint on '%s'; "
                "falling back to real %s constraint %s.", proposed, table, ctype, cols)
            return cols

    # 3. Nothing usable — return proposed and let the DB raise a clear error.
    logger.error("Promotion: no usable unique constraint on '%s' for ON CONFLICT "
                 "(proposed=%s, available=%s).", table, proposed, available_cols)
    return proposed


async def _promote_rows(
    conn, tid: uuid_mod.UUID, risk_level: str,
    approved_by: str, new_status: str, config: dict
) -> int:
    """
    Generic promotion: reads staging rows and writes to entity + records tables
    using the agent's promotion config. Works for any schema the agent mapped.
    """
    entity_cfg  = config.get("entity_table",  {})
    records_cfg = config.get("records_table", {})

    if not entity_cfg or not records_cfg:
        return 0

    entity_tbl   = _validate_identifier(entity_cfg["name"],        "entity_table.name")
    entity_map   = entity_cfg["column_map"]
    upsert_key   = entity_cfg["upsert_key"]
    entity_pk    = _validate_identifier(entity_cfg["pk"],          "entity_table.pk")

    if upsert_key in entity_map:
        upsert_key = entity_map[upsert_key]
    _validate_identifier(upsert_key, "entity_table.upsert_key")
    for col in entity_map.values():
        _validate_identifier(col, f"entity_table.column_map value '{col}'")

    records_tbl  = _validate_identifier(records_cfg["name"],       "records_table.name")
    records_map  = records_cfg["column_map"]
    fk_col       = _validate_identifier(records_cfg["fk_column"],  "records_table.fk_column")
    upsert_keys  = records_cfg["upsert_keys"]
    for col in upsert_keys:
        _validate_identifier(col, f"records_table.upsert_keys value '{col}'")
    for col in records_map.values():
        _validate_identifier(col, f"records_table.column_map value '{col}'")

    staging_tbl = _validate_identifier(_staging_table(config),     "staging_table")
    staged_rows = await conn.fetch(f"""
        SELECT * FROM {staging_tbl}
        WHERE  trace_id=$1 AND status='pending' AND risk_level=$2
    """, tid, risk_level)

    if not staged_rows:
        return 0

    promoted = 0
    for row in staged_rows:
        row_dict = dict(row)
        data     = row_dict["data"] if isinstance(row_dict["data"], dict) \
                   else json_mod.loads(row_dict["data"])

        # 1. Upsert entity (e.g. gds_users)
        entity_cols = list(entity_map.values())
        try:
            entity_vals = [_coerce(data[src]) for src in entity_map.keys()]
        except KeyError as e:
            raise RuntimeError(
                f"promotion_config entity_table.column_map key {e} not found in staging data. "
                f"Staging data keys: {list(data.keys())}. "
                f"column_map keys: {list(entity_map.keys())}"
            )

        entity_conflict = await _resolve_conflict_target(
            conn, entity_tbl, [upsert_key], entity_cols)
        update_cols   = [c for c in entity_cols if c not in entity_conflict]
        update_clause = ", ".join(f"{c}=EXCLUDED.{c}" for c in update_cols)
        on_conflict   = f"DO UPDATE SET {update_clause}" if update_clause else "DO NOTHING"

        insert_entity = f"""
            INSERT INTO {entity_tbl} ({", ".join(entity_cols)})
            VALUES ({", ".join(f"${i+1}" for i in range(len(entity_cols)))})
            ON CONFLICT ({", ".join(entity_conflict)}) {on_conflict}
            RETURNING {entity_pk}
        """
        ent_row = await conn.fetchrow(insert_entity, *entity_vals)
        entity_id = ent_row[entity_pk]

        # 2. Insert record (e.g. gds_experiments)
        _infra = {"trace_id", "approved_by", fk_col, "source_experiment_id"}
        filtered_map = {src: tgt for src, tgt in records_map.items() if tgt not in _infra}
        rec_cols = [fk_col] + list(filtered_map.values()) + ["trace_id", "approved_by"]
        try:
            rec_vals = [entity_id] + [_coerce(data[src]) for src in filtered_map.keys()] \
                       + [tid, approved_by]
        except KeyError as e:
            raise RuntimeError(
                f"promotion_config records_table.column_map key {e} not found in staging data. "
                f"Staging data keys: {list(data.keys())}. "
                f"column_map keys: {list(records_map.keys())}"
            )

        # Append source_experiment_id if agent included it in staging data
        src_exp_id = data.get("source_experiment_id")
        if src_exp_id is not None:
            rec_cols.append("source_experiment_id")
            rec_vals.append(str(src_exp_id))

        # Guard: the agent's upsert_keys must match a real unique constraint, else
        # ON CONFLICT raises. Resolve against the table's actual constraints.
        conflict_keys = await _resolve_conflict_target(
            conn, records_tbl, upsert_keys, rec_cols)
        uk_clause = ", ".join(f"{c}=EXCLUDED.{c}" for c in rec_cols
                              if c not in conflict_keys)

        insert_rec = f"""
            INSERT INTO {records_tbl} ({", ".join(rec_cols)})
            VALUES ({", ".join(f"${i+1}" for i in range(len(rec_vals)))})
            ON CONFLICT ({", ".join(conflict_keys)}) DO UPDATE SET {uk_clause}
        """
        await conn.execute(insert_rec, *rec_vals)
        promoted += 1

    await conn.execute(f"""
        UPDATE {staging_tbl}
        SET    status = $3
        WHERE  trace_id = $1 AND status = 'pending' AND risk_level = $2
    """, tid, risk_level, new_status)

    return promoted


async def auto_approve_clean_rows(trace_id: str, approved_by: str = "AI Agent") -> dict:
    """
    Partial HITL — auto-promote risk_level='auto' rows to production.
    Uses the agent's promotion config (generic — works for any schema).
    """
    pool = await get_gds_pool()
    tid  = uuid_mod.UUID(trace_id)

    async with pool.acquire() as conn:
        config = await _load_promotion_config(conn, tid)
        async with conn.transaction():
            promoted = await _promote_rows(
                conn, tid, risk_level="auto",
                approved_by=approved_by, new_status="auto_approved",
                config=config,
            )
        staging_tbl = _validate_identifier(_staging_table(config), "staging_table")
        pending_review = await conn.fetchval(f"""
            SELECT COUNT(*) FROM {staging_tbl}
            WHERE  trace_id=$1 AND status='pending' AND risk_level='review'
        """, tid)
        if promoted:
            await conn.execute("""
                INSERT INTO migration_audit_log
                    (trace_id, event, promoted_count, approved_by)
                VALUES ($1, 'auto_approved', $2, $3)
            """, tid, promoted, approved_by)

    return {"auto_promoted": promoted, "pending_review": int(pending_review)}


def _serialize(row: dict) -> dict:
    out = {}
    for k, v in row.items():
        if isinstance(v, uuid_mod.UUID):
            out[k] = str(v)
        elif hasattr(v, "isoformat"):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out


class ApproveRequest(BaseModel):
    approved_by: str = "HITL Reviewer"


class ExcludeUserRequest(BaseModel):
    scientist_name: str


# ── GET /api/migrate/pending ─────────────────────────────────────────────────

@router.get("/pending")
async def get_pending_runs():
    """
    Dashboard endpoint — returns all migration runs that have pending review wells,
    grouped by trace_id with per-scientist summaries.
    """
    pool = await get_gds_pool()

    async with pool.acquire() as conn:
        # Collect every staging table referenced by any migration plan (+ default).
        # Different runs could in principle use different staging tables.
        plan_rows = await conn.fetch("SELECT plan_json FROM migration_plans")
        staging_tables = {"gds_staging_experiments"}
        for pr in plan_rows:
            plan = json_mod.loads(pr["plan_json"]) if isinstance(pr["plan_json"], str) \
                   else pr["plan_json"]
            st = (plan or {}).get("staging_table")
            if st:
                staging_tables.add(st)

        rows = []
        for st in sorted(staging_tables):
            try:
                _validate_identifier(st, "staging_table")
            except ValueError:
                continue  # skip any unsafe table name from a malformed plan
            rows += await conn.fetch(f"""
                SELECT trace_id, data, status, risk_level, created_at
                FROM   {st}
                WHERE  status = 'pending' AND risk_level = 'review'
                ORDER  BY created_at DESC
            """)

    if not rows:
        return JSONResponse(content={"runs": []})

    runs: dict = {}
    for r in rows:
        tid  = str(r["trace_id"])
        data = r["data"] if isinstance(r["data"], dict) else json_mod.loads(r["data"])

        scientist = pick_field(data, NAME_FIELDS, "Unknown")

        if tid not in runs:
            runs[tid] = {
                "trace_id":   tid,
                "created_at": r["created_at"].isoformat() if hasattr(r["created_at"], "isoformat") else str(r["created_at"]),
                "scientists": {},
            }

        if scientist not in runs[tid]["scientists"]:
            runs[tid]["scientists"][scientist] = {"name": scientist, "pending_wells": 0}
        runs[tid]["scientists"][scientist]["pending_wells"] += 1

    result = []
    for run in runs.values():
        run["scientists"] = list(run["scientists"].values())
        run["total_pending"] = sum(s["pending_wells"] for s in run["scientists"])
        result.append(run)

    return JSONResponse(content={"runs": result})


# ── GET /api/migrate/staging/{trace_id} ──────────────────────────────────────

@router.get("/staging/{trace_id}")
async def get_staging(trace_id: str, request: Request):
    """Staged dataset for HITL review — shows only rows that need human attention."""
    pool = await get_gds_pool()
    async with pool.acquire() as conn:
        config = await _load_promotion_config(conn, uuid_mod.UUID(trace_id))
        staging_tbl = _staging_table(config)
        rows = await conn.fetch(f"""
            SELECT staging_id, trace_id, data, risk_level, status, created_at
            FROM   {staging_tbl}
            WHERE  trace_id = $1
            ORDER  BY created_at
        """, uuid_mod.UUID(trace_id))

    if not rows:
        raise HTTPException(status_code=404,
            detail=f"No staged rows found for trace_id={trace_id}")

    serialized = []
    for r in rows:
        row_dict = _serialize(dict(r))
        data     = row_dict.pop("data") if isinstance(row_dict["data"], dict) \
                   else json_mod.loads(row_dict.pop("data"))
        row_dict.update(data)

        # Normalize display fields — agent may use different column names
        if not row_dict.get("scientist_name"):
            row_dict["scientist_name"] = pick_field(row_dict, NAME_FIELDS, "Unknown")
        if not row_dict.get("well_position"):
            row_dict["well_position"] = pick_field(row_dict, WELL_FIELDS)
        if row_dict.get("signal") is None:
            row_dict["signal"] = pick_field(row_dict, SIGNAL_FIELDS)
        if not row_dict.get("scientist_role"):
            row_dict["scientist_role"] = pick_field(row_dict, ROLE_FIELDS)

        serialized.append(row_dict)

    auto_approved = sum(1 for r in serialized if r.get("risk_level") == "auto")
    needs_review  = sum(1 for r in serialized if r.get("risk_level") == "review" and r["status"] == "pending")

    # Build per-scientist summary
    users_summary: dict = {}
    for r in serialized:
        sname = r["scientist_name"]
        if sname not in users_summary:
            users_summary[sname] = {"name": sname, "role": r.get("scientist_role", ""), "row_count": 0, "avg_signal": 0.0, "_signals": []}
        users_summary[sname]["row_count"] += 1
        if r.get("signal") is not None:
            users_summary[sname]["_signals"].append(float(r["signal"]))

    for u in users_summary.values():
        sigs = u.pop("_signals")
        u["avg_signal"] = round(sum(sigs) / len(sigs), 4) if sigs else 0.0

    return JSONResponse(content={
        "trace_id":     trace_id,
        "row_count":    len(rows),
        "auto_approved": auto_approved,
        "needs_review":  needs_review,
        "users":        list(users_summary.values()),
        "rows":         serialized,
    })


# ── POST /api/migrate/approve/{trace_id} ─────────────────────────────────────

@router.post("/approve/{trace_id}")
async def approve(trace_id: str, body: ApproveRequest, request: Request):
    """
    HITL Approval — promotes pending review rows to production tables:
      1. Upsert each entity row
      2. Insert every record row with FK
      3. Mark staging rows 'approved'
      4. Write audit log
    """
    log  = logging.LoggerAdapter(logger, {"trace_id": trace_id})
    pool = await get_gds_pool()
    tid  = uuid_mod.UUID(trace_id)

    async with pool.acquire() as conn:
        config      = await _load_promotion_config(conn, tid)
        staging_tbl = _validate_identifier(_staging_table(config), "staging_table")

        pending_count = await conn.fetchval(f"""
            SELECT COUNT(*) FROM {staging_tbl}
            WHERE  trace_id=$1 AND status='pending' AND risk_level='review'
        """, tid)

        if not pending_count:
            raise HTTPException(status_code=404,
                detail=f"No rows pending human review for trace_id={trace_id}.")

        promoted_experiments = 0

        async with conn.transaction():
            promoted_experiments = await _promote_rows(
                conn, tid, risk_level="review",
                approved_by=body.approved_by, new_status="approved",
                config=config,
            )

            excluded_count = await conn.fetchval(
                f"SELECT COUNT(*) FROM {staging_tbl} WHERE trace_id=$1 AND status='excluded'",
                tid,
            )
            await conn.execute("""
                INSERT INTO migration_audit_log
                    (trace_id, event, staged_count, promoted_count, excluded_count, approved_by)
                VALUES ($1, 'hitl_approved', $2, $3, $4, $5)
            """, tid,
                int(pending_count),
                promoted_experiments,
                int(excluded_count),
                body.approved_by)

    log.info("Approved: %d experiments, approver=%s", promoted_experiments, body.approved_by)

    # Delete fully-migrated scientists from ABASE
    # Only delete scientists who had NO excluded rows in this run
    abase_deleted = 0
    try:
        async with pool.acquire() as conn:
            all_rows = await conn.fetch(f"""
                SELECT COALESCE(
                    data->>'scientist_name',
                    data->>'name',
                    data->>'scientist',
                    data->>'user_name'
                ) AS sname, status
                FROM {staging_tbl} WHERE trace_id=$1
            """, tid)
        scientists: dict[str, set] = {}
        for r in all_rows:
            sname = r["sname"] or "Unknown"
            scientists.setdefault(sname, set()).add(r["status"])

        to_delete = [name for name, statuses in scientists.items()
                     if "excluded" not in statuses and name != "Unknown"]

        # Guard #6: never delete a scientist who still has pending wells in ANY trace.
        # A scientist may appear in multiple migration runs — deleting by name alone
        # would remove them from ABASE while another run still needs them.
        if to_delete:
            async with pool.acquire() as conn:
                blocked_rows = await conn.fetch(f"""
                    SELECT DISTINCT COALESCE(
                        data->>'scientist_name', data->>'name',
                        data->>'scientist', data->>'user_name'
                    ) AS sname
                    FROM {staging_tbl}
                    WHERE status = 'pending'
                """)
            blocked = {r["sname"] for r in blocked_rows if r["sname"]}
            skipped = [n for n in to_delete if n in blocked]
            to_delete = [n for n in to_delete if n not in blocked]
            if skipped:
                log.info("Kept in ABASE (pending wells in other runs): %s", skipped)

        if to_delete:
            abase_pool = await get_abase_pool()
            async with abase_pool.acquire() as aconn:
                result = await aconn.execute(
                    "DELETE FROM users WHERE name = ANY($1::text[])", to_delete
                )
                abase_deleted = int(result.split()[-1])
                log.info("Deleted %d scientists from ABASE: %s", abase_deleted, to_delete)
    except Exception as e:
        log.warning("ABASE cleanup skipped: %s", e)

    return JSONResponse(content={
        "trace_id":             trace_id,
        "experiments_promoted": promoted_experiments,
        "approved_by":          body.approved_by,
        "abase_deleted":        abase_deleted,
        "status":               "approved",
    })


# ── POST /api/migrate/reject/{trace_id} ──────────────────────────────────────

@router.post("/reject/{trace_id}")
async def reject(trace_id: str, request: Request):
    """Reject staging — nothing reaches GDS production."""
    log = logging.LoggerAdapter(logger, {"trace_id": trace_id})
    pool = await get_gds_pool()
    tid  = uuid_mod.UUID(trace_id)

    # Capture WHO rejected — so the action is attributed in the audit trail / report.
    try:
        body = await request.json()
    except Exception:
        body = {}
    rejected_by = (body or {}).get("approved_by") or "HITL Reviewer"

    async with pool.acquire() as conn:
        config      = await _load_promotion_config(conn, tid)
        staging_tbl = _staging_table(config)
        result = await conn.execute(
            f"UPDATE {staging_tbl} SET status='rejected' WHERE trace_id=$1 AND status='pending'",
            tid,
        )
        rows_rejected = int(result.split()[-1])

        if not rows_rejected:
            raise HTTPException(status_code=404,
                detail=f"No pending rows to reject for trace_id={trace_id}.")

        await conn.execute("""
            INSERT INTO migration_audit_log
                (trace_id, event, staged_count, excluded_count, promoted_count, approved_by)
            VALUES ($1, 'hitl_rejected', $2, $2, 0, $3)
        """, tid, rows_rejected, rejected_by)

    log.info("Rejected %d staging rows for trace_id=%s by %s", rows_rejected, trace_id, rejected_by)
    return JSONResponse(content={
        "trace_id":      trace_id,
        "rejected_rows": rows_rejected,
        "status":        "rejected",
        "rejected_by":   rejected_by,
    })


# ── POST /api/migrate/rollback/{trace_id} ────────────────────────────────────

@router.post("/rollback/{trace_id}")
async def rollback(trace_id: str, request: Request):
    """
    Rollback an approved migration:
      - Deletes gds_experiments records for this trace
      - Marks staging rows as 'rolled_back'
      - Does NOT delete gds_users (they may appear in other migrations)
    """
    log = logging.LoggerAdapter(logger, {"trace_id": trace_id})
    pool = await get_gds_pool()
    tid  = uuid_mod.UUID(trace_id)

    async with pool.acquire() as conn:
        config      = await _load_promotion_config(conn, tid)
        staging_tbl = _staging_table(config)
        records_tbl = config.get("records_table", {}).get("name", "gds_experiments")

        approved_count = await conn.fetchval(
            f"SELECT COUNT(*) FROM {records_tbl} WHERE trace_id=$1", tid)

        if not approved_count:
            raise HTTPException(status_code=404,
                detail=f"No approved experiments found for trace_id={trace_id}.")

        async with conn.transaction():
            deleted = await conn.execute(
                f"DELETE FROM {records_tbl} WHERE trace_id=$1", tid)
            rows_deleted = int(deleted.split()[-1])

            await conn.execute(f"""
                UPDATE {staging_tbl}
                SET    status = 'rolled_back'
                WHERE  trace_id = $1 AND status = 'approved'
            """, tid)

            await conn.execute("""
                INSERT INTO migration_audit_log (trace_id, event)
                VALUES ($1, 'rolled_back')
            """, tid)

    log.info("Rolled back %d experiments for trace_id=%s", rows_deleted, trace_id)
    return JSONResponse(content={
        "trace_id":         trace_id,
        "experiments_deleted": rows_deleted,
        "status":           "rolled_back",
    })


# ── GET /api/migrate/audit/{trace_id} ────────────────────────────────────────

@router.get("/audit/{trace_id}")
async def get_audit(trace_id: str):
    """Full audit record for a migration trace."""
    pool = await get_gds_pool()
    tid  = uuid_mod.UUID(trace_id)

    async with pool.acquire() as conn:
        records = await conn.fetch(
            "SELECT * FROM migration_audit_log WHERE trace_id=$1 ORDER BY created_at",
            tid)

        config      = await _load_promotion_config(conn, tid)
        staging_tbl = _validate_identifier(_staging_table(config), "staging_table")
        staging_summary = await conn.fetch(f"""
            SELECT status, COUNT(*) AS count
            FROM   {staging_tbl}
            WHERE  trace_id = $1
            GROUP  BY status
        """, tid)

    if not records:
        raise HTTPException(status_code=404,
            detail=f"No audit records found for trace_id={trace_id}.")

    return JSONResponse(content={
        "audit":   [_serialize(dict(r)) for r in records],
        "staging": {r["status"]: r["count"] for r in staging_summary},
    })


# ── HITL surgical controls ────────────────────────────────────────────────────

@router.post("/staging/{trace_id}/exclude-user")
async def exclude_user(trace_id: str, body: ExcludeUserRequest, request: Request):
    """Mark all rows for a scientist as excluded."""
    pool = await get_gds_pool()
    tid  = uuid_mod.UUID(trace_id)
    async with pool.acquire() as conn:
        config      = await _load_promotion_config(conn, tid)
        staging_tbl = _staging_table(config)
        result = await conn.execute(f"""
            UPDATE {staging_tbl}
            SET    status = 'excluded'
            WHERE  trace_id = $1 AND status = 'pending'
              AND (
                data->>'scientist_name' = $2
                OR data->>'name'        = $2
                OR data->>'scientist'   = $2
                OR data->>'user_name'   = $2
              )
        """, tid, body.scientist_name)
    return JSONResponse(content={
        "trace_id":       trace_id,
        "scientist_name": body.scientist_name,
        "excluded_rows":  int(result.split()[-1]),
    })


@router.post("/staging/{trace_id}/restore-user")
async def restore_user(trace_id: str, body: ExcludeUserRequest, request: Request):
    """Restore an excluded scientist back to pending."""
    pool = await get_gds_pool()
    tid  = uuid_mod.UUID(trace_id)
    async with pool.acquire() as conn:
        config      = await _load_promotion_config(conn, tid)
        staging_tbl = _staging_table(config)
        result = await conn.execute(f"""
            UPDATE {staging_tbl}
            SET    status = 'pending'
            WHERE  trace_id = $1 AND status = 'excluded'
              AND (
                data->>'scientist_name' = $2
                OR data->>'name'        = $2
                OR data->>'scientist'   = $2
                OR data->>'user_name'   = $2
              )
        """, tid, body.scientist_name)
    return JSONResponse(content={
        "trace_id":       trace_id,
        "scientist_name": body.scientist_name,
        "restored_rows":  int(result.split()[-1]),
    })


@router.delete("/staging/row/{staging_id}")
async def exclude_row(staging_id: int, trace_id: str, request: Request):
    """Exclude a single row from the migration."""
    pool = await get_gds_pool()
    tid  = uuid_mod.UUID(trace_id)
    async with pool.acquire() as conn:
        config      = await _load_promotion_config(conn, tid)
        staging_tbl = _staging_table(config)
        result = await conn.execute(f"""
            UPDATE {staging_tbl}
            SET    status = 'excluded'
            WHERE  staging_id = $1 AND status = 'pending'
        """, staging_id)
    if int(result.split()[-1]) == 0:
        raise HTTPException(status_code=404,
            detail=f"Row {staging_id} not found or already processed.")
    return JSONResponse(content={"staging_id": staging_id, "status": "excluded"})


@router.post("/staging/row/{staging_id}/restore")
async def restore_row(staging_id: int, trace_id: str, request: Request):
    """Restore a single excluded row to pending."""
    pool = await get_gds_pool()
    tid  = uuid_mod.UUID(trace_id)
    async with pool.acquire() as conn:
        config      = await _load_promotion_config(conn, tid)
        staging_tbl = _staging_table(config)
        result = await conn.execute(f"""
            UPDATE {staging_tbl}
            SET    status = 'pending'
            WHERE  staging_id = $1 AND status = 'excluded'
        """, staging_id)
    if int(result.split()[-1]) == 0:
        raise HTTPException(status_code=404,
            detail=f"Row {staging_id} not found or not excluded.")
    return JSONResponse(content={"staging_id": staging_id, "status": "pending"})
