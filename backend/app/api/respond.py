"""
Scientist self-serve review — public, token-scoped endpoints
============================================================
Front of the notify-and-resolve loop. The reviewer mints a magic-link token per
flagged scientist; the scientist opens the link, sees ONLY their own flagged wells,
and replies in plain language. The reply is handed to the Review Resolution Agent
(app/agents/review_agent.py), which interprets it into approve/exclude actions.

Routes (prefix /api/respond):
  POST /notify/{trace_id}     reviewer — mint/refresh invitations for flagged scientists
  GET  /status/{trace_id}     reviewer — per-scientist invitation status (queue UI)
  GET  /{token}               scientist — their flagged wells (token-scoped)
  POST /{token}               scientist — submit reply → run the agent

The `review_invitations` table auto-creates on first use (no manual SQL needed in dev).
"""

import uuid as uuid_mod
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.connectors import get_gds_pool
from app.core.notify import mint_token, build_link, send_invitation, INVITE_TTL, PROVIDER
from app.agents.review_agent import (
    run_review_agent,
    load_promotion_config,
    _tool_get_scientist_wells,
)

router = APIRouter(prefix="/api/respond", tags=["respond"])
logger = logging.getLogger(__name__)

# Same schema-agnostic owner filter the Review Resolution Agent uses.
_NAME_FILTER = """(
    data->>'scientist_name' = $2
    OR data->>'name' = $2
    OR data->>'scientist' = $2
    OR data->>'user_name' = $2
)"""


# ── Table bootstrap (auto-create, like backup_store) ──────────────────────────

async def _ensure_table(conn) -> None:
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS review_invitations (
            id             BIGSERIAL   PRIMARY KEY,
            token          TEXT        NOT NULL UNIQUE,
            trace_id       UUID        NOT NULL,
            scientist_name TEXT        NOT NULL,
            status         TEXT        NOT NULL DEFAULT 'notified',
            channel        TEXT        NOT NULL DEFAULT 'console',
            reply_text     TEXT,
            sent_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            responded_at   TIMESTAMPTZ,
            expires_at     TIMESTAMPTZ NOT NULL DEFAULT (NOW() + INTERVAL '7 days'),
            CONSTRAINT uq_invite_trace_scientist UNIQUE (trace_id, scientist_name)
        )
    """)
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_invitations_token ON review_invitations (token)")


async def _flagged_scientists(conn, tid, staging_tbl: str, only: str | None) -> list[str]:
    """Distinct scientists who still have pending review-flagged wells in this run."""
    if only:
        rows = await conn.fetch(f"""
            SELECT DISTINCT data->>'scientist_name' AS n
            FROM   {staging_tbl}
            WHERE  trace_id=$1 AND status='pending' AND risk_level='review' AND {_NAME_FILTER}
        """, tid, only)
        return [only] if rows else []
    rows = await conn.fetch(f"""
        SELECT DISTINCT
            COALESCE(data->>'scientist_name', data->>'name',
                     data->>'scientist',      data->>'user_name') AS n
        FROM   {staging_tbl}
        WHERE  trace_id=$1 AND status='pending' AND risk_level='review'
    """, tid)
    return [r["n"] for r in rows if r["n"]]


# ── POST /api/respond/notify/{trace_id} ───────────────────────────────────────

class NotifyRequest(BaseModel):
    scientist_name: str | None = None  # None → notify every flagged scientist


@router.post("/notify/{trace_id}")
async def notify(trace_id: str, body: NotifyRequest | None = None):
    """Mint (or refresh) a magic-link invitation per flagged scientist and dispatch it."""
    tid  = uuid_mod.UUID(trace_id)
    only = body.scientist_name if body else None

    pool   = await get_gds_pool()
    config = await load_promotion_config(pool, tid)
    staging_tbl = config.get("staging_table")
    if not staging_tbl:
        raise RuntimeError(
            "promotion_config missing 'staging_table'. "
            "Agent must discover and specify the target staging table."
        )

    async with pool.acquire() as conn:
        await _ensure_table(conn)
        scientists = await _flagged_scientists(conn, tid, staging_tbl, only)

        if not scientists:
            return JSONResponse(content={
                "trace_id": trace_id, "notified": [],
                "note": "No scientists with pending flagged wells.",
            })

        notified = []
        for name in scientists:
            # Count this scientist's pending flagged wells for the message.
            pending = await conn.fetchval(f"""
                SELECT COUNT(*) FROM {staging_tbl}
                WHERE trace_id=$1 AND status='pending' AND risk_level='review' AND {_NAME_FILTER}
            """, tid, name)

            # Reuse an existing token if present; otherwise mint a fresh one.
            token = await conn.fetchval(
                "SELECT token FROM review_invitations WHERE trace_id=$1 AND scientist_name=$2",
                tid, name)
            if not token:
                token = mint_token()

            await conn.execute(f"""
                INSERT INTO review_invitations
                    (token, trace_id, scientist_name, status, channel, sent_at, expires_at)
                VALUES ($1, $2, $3, 'notified', $4, NOW(), NOW() + INTERVAL '{INVITE_TTL} days')
                ON CONFLICT (trace_id, scientist_name) DO UPDATE
                    SET status='notified', channel=EXCLUDED.channel,
                        sent_at=NOW(), expires_at=NOW() + INTERVAL '{INVITE_TTL} days'
            """, token, tid, name, PROVIDER)

            link = build_link(token)
            delivery = await send_invitation(name, link, int(pending or 0))
            notified.append({
                "scientist_name": name, "pending_wells": int(pending or 0), **delivery,
            })

    return JSONResponse(content={"trace_id": trace_id, "provider": PROVIDER, "notified": notified})


# ── GET /api/respond/status/{trace_id} ────────────────────────────────────────

@router.get("/status/{trace_id}")
async def status(trace_id: str):
    """Per-scientist invitation status for the reviewer queue UI."""
    tid  = uuid_mod.UUID(trace_id)
    pool = await get_gds_pool()
    async with pool.acquire() as conn:
        await _ensure_table(conn)
        rows = await conn.fetch("""
            SELECT scientist_name, status, channel, sent_at, responded_at, expires_at,
                   (expires_at < NOW()) AS expired
            FROM   review_invitations
            WHERE  trace_id=$1
            ORDER  BY scientist_name
        """, tid)
    invitations = [{
        "scientist_name": r["scientist_name"],
        "status":         "expired" if r["expired"] and r["status"] == "notified" else r["status"],
        "channel":        r["channel"],
        "sent_at":        r["sent_at"].isoformat() if r["sent_at"] else None,
        "responded_at":   r["responded_at"].isoformat() if r["responded_at"] else None,
        "expires_at":     r["expires_at"].isoformat() if r["expires_at"] else None,
    } for r in rows]
    return JSONResponse(content={"trace_id": trace_id, "invitations": invitations})


# ── Token lookup helper ───────────────────────────────────────────────────────

async def _load_invitation(conn, token: str) -> dict:
    row = await conn.fetchrow(
        "SELECT trace_id, scientist_name, status, expires_at, "
        "       (expires_at < NOW()) AS expired "
        "FROM review_invitations WHERE token=$1", token)
    if not row:
        raise HTTPException(status_code=404, detail="Invalid or unknown link.")
    if row["expired"]:
        raise HTTPException(status_code=410, detail="This link has expired. Please contact the data team.")
    return dict(row)


# ── GET /api/respond/{token} ──────────────────────────────────────────────────

@router.get("/{token}")
async def get_my_wells(token: str):
    """Scientist-facing: load ONLY this scientist's flagged wells, scoped by the token."""
    pool = await get_gds_pool()
    async with pool.acquire() as conn:
        await _ensure_table(conn)
        inv = await _load_invitation(conn, token)

    tid    = inv["trace_id"]
    name   = inv["scientist_name"]
    config = await load_promotion_config(pool, tid)
    wells  = await _tool_get_scientist_wells(pool, tid, name, config)
    if wells.get("status") == "not_found":
        raise HTTPException(status_code=404, detail=wells["error"])

    return JSONResponse(content={
        "scientist_name": name,
        "status":         inv["status"],
        "pending_wells":  wells.get("pending_wells", 0),
        "wells":          wells.get("wells", []),
    })


# ── POST /api/respond/{token} ─────────────────────────────────────────────────

class ReplyRequest(BaseModel):
    # Either send free-text `message`, or structured `decisions` (+ optional `note`).
    message:   str | None = None
    decisions: dict[str, str] | None = None  # {"B03": "drop", "B04": "keep"}
    note:      str | None = None


def _compose_message(body: ReplyRequest) -> str:
    """
    Turn the hybrid page's input (per-well Keep/Drop toggles + an optional note) into a
    single natural-language instruction for the Review Resolution Agent. The agent still
    reasons over it — the note can add nuance the toggles don't capture.
    """
    if body.message and body.message.strip():
        return body.message.strip()

    decisions = body.decisions or {}
    keeps = [w for w, d in decisions.items() if d == "keep"]
    drops = [w for w, d in decisions.items() if d == "drop"]
    parts = []
    if keeps:
        parts.append(f"Keep (migrate) wells: {', '.join(sorted(keeps))}.")
    if drops:
        parts.append(f"Exclude (do not migrate) wells: {', '.join(sorted(drops))}.")
    if body.note and body.note.strip():
        parts.append(f"Reason to keep (scientist's justification): {body.note.strip()}")
    return " ".join(parts)


@router.post("/{token}")
async def submit_reply(token: str, body: ReplyRequest):
    """
    Scientist-facing: submit a decision (free-text reply, or Keep/Drop toggles + note).
    The Review Resolution Agent interprets it and resolves this scientist's pending wells,
    attributed to the scientist.
    """
    message = _compose_message(body)
    if not message.strip():
        raise HTTPException(status_code=400, detail="No decision provided.")

    pool = await get_gds_pool()
    async with pool.acquire() as conn:
        await _ensure_table(conn)
        inv = await _load_invitation(conn, token)

    tid  = inv["trace_id"]
    name = inv["scientist_name"]
    log  = logging.LoggerAdapter(logger, {"trace_id": str(tid)})
    log.info("Self-serve reply: scientist=%s", name)

    # The action is attributed to the scientist — better provenance than "HITL Reviewer".
    result = await run_review_agent(
        trace_id=str(tid),
        scientist_name=name,
        admin_message=message,
        approved_by=name,
    )

    # Record the reply as audit evidence; mark resolved iff no pending wells remain.
    config      = await load_promotion_config(pool, tid)
    staging_tbl = config.get("staging_table")
    if not staging_tbl:
        raise RuntimeError(
            "promotion_config missing 'staging_table'. "
            "Agent must discover and specify the target staging table."
        )
    async with pool.acquire() as conn:
        remaining = await conn.fetchval(f"""
            SELECT COUNT(*) FROM {staging_tbl}
            WHERE trace_id=$1 AND status='pending' AND {_NAME_FILTER}
        """, tid, name)
        new_status = "resolved" if int(remaining or 0) == 0 else "replied"
        await conn.execute("""
            UPDATE review_invitations
            SET    reply_text=$2, responded_at=$3, status=$4
            WHERE  token=$1
        """, token, message, datetime.now(timezone.utc), new_status)

    result["invitation_status"] = new_status
    result["pending_remaining"] = int(remaining or 0)
    return JSONResponse(content=result)
