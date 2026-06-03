"""
Backup Orchestrator
====================
Triggers a cloud infrastructure snapshot BEFORE migration starts.
The agent never reads or copies data rows — it calls the configured
backup provider and records the returned snapshot ID.

Supported providers (set BACKUP_PROVIDER env var):
  aws_rds   — AWS RDS CreateDBSnapshot via boto3
  supabase  — Supabase Management API
  webhook   — Generic HTTP webhook (enterprise backup managers, Oracle, etc.)
  none      — No backup (development only; logs a warning)

Required env vars per provider:
  aws_rds:
    BACKUP_AWS_DB_INSTANCE_ID
    BACKUP_AWS_REGION              (default: us-west-2)
    BACKUP_AWS_ACCESS_KEY_ID
    BACKUP_AWS_SECRET_ACCESS_KEY

  supabase:
    BACKUP_SUPABASE_PROJECT_REF
    BACKUP_SUPABASE_ACCESS_TOKEN

  webhook:
    BACKUP_WEBHOOK_URL
    BACKUP_WEBHOOK_TOKEN           (optional bearer token)
"""

import os
import asyncio
import logging
import urllib.parse

logger = logging.getLogger(__name__)

PROVIDER      = os.getenv("BACKUP_PROVIDER", "none")
POLL_INTERVAL = int(os.getenv("BACKUP_POLL_INTERVAL_SEC", "15"))   # seconds between status checks
POLL_TIMEOUT  = int(os.getenv("BACKUP_POLL_TIMEOUT_SEC",  "600"))  # max wait before giving up (10 min)


async def trigger_backup(source_url: str, trace_id: str) -> dict:
    """
    Trigger a snapshot via the configured provider.
    Returns {"provider", "snapshot_id", "status", "metadata"}.
    Raises RuntimeError on failure — caller decides whether to abort the migration.
    """
    if PROVIDER == "aws_rds":
        return await _aws_rds_snapshot(trace_id)
    elif PROVIDER == "supabase":
        return await _supabase_backup(trace_id)
    elif PROVIDER == "webhook":
        return await _webhook_backup(source_url, trace_id)
    elif PROVIDER == "none":
        logger.warning(
            "BACKUP_PROVIDER=none — no snapshot triggered for trace=%s. "
            "Set BACKUP_PROVIDER in .env before running in production.", trace_id)
        return {"provider": "none", "snapshot_id": None,
                "status": "skipped", "metadata": {}}
    else:
        raise RuntimeError(f"Unknown BACKUP_PROVIDER='{PROVIDER}'. "
                           "Valid values: aws_rds, supabase, webhook, none.")


async def check_backup_status(snapshot_id: str, provider: str) -> dict:
    """
    Poll the backup provider until the snapshot is complete, failed, or timed out.
    Returns {"status": "available" | "failed" | "timeout", "message": ...}
    """
    if provider == "aws_rds":
        return await _aws_rds_poll(snapshot_id)
    elif provider == "supabase":
        return await _supabase_poll(snapshot_id)
    elif provider == "webhook":
        return await _webhook_poll(snapshot_id)
    elif provider == "none":
        return {"status": "skipped", "message": "No backup provider configured."}
    else:
        return {"status": "failed", "message": f"Unknown provider: {provider}"}


async def _aws_rds_poll(snapshot_id: str) -> dict:
    import boto3

    client = boto3.client(
        "rds",
        region_name=os.getenv("BACKUP_AWS_REGION", "us-west-2"),
        aws_access_key_id=os.getenv("BACKUP_AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("BACKUP_AWS_SECRET_ACCESS_KEY"),
    )
    elapsed = 0
    while elapsed < POLL_TIMEOUT:
        resp   = client.describe_db_snapshots(DBSnapshotIdentifier=snapshot_id)
        state  = resp["DBSnapshots"][0]["Status"]   # creating | available | failed | deleted
        logger.info("AWS RDS snapshot %s status: %s (%ds elapsed)", snapshot_id, state, elapsed)
        if state == "available":
            return {"status": "available", "message": f"Snapshot {snapshot_id} complete."}
        if state in ("failed", "deleted"):
            return {"status": "failed", "message": f"Snapshot {snapshot_id} state={state}."}
        await asyncio.sleep(POLL_INTERVAL)
        elapsed += POLL_INTERVAL
    return {"status": "timeout", "message": f"Snapshot {snapshot_id} not complete after {POLL_TIMEOUT}s."}


async def _supabase_poll(snapshot_id: str) -> dict:
    import httpx

    project_ref  = os.environ["BACKUP_SUPABASE_PROJECT_REF"]
    access_token = os.environ["BACKUP_SUPABASE_ACCESS_TOKEN"]
    elapsed = 0
    async with httpx.AsyncClient() as client:
        while elapsed < POLL_TIMEOUT:
            resp = await client.get(
                f"https://api.supabase.com/v1/projects/{project_ref}/database/backups/{snapshot_id}",
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=30,
            )
            resp.raise_for_status()
            data   = resp.json()
            state  = data.get("status", "")
            logger.info("Supabase backup %s status: %s (%ds elapsed)", snapshot_id, state, elapsed)
            if state in ("completed", "success", "available"):
                return {"status": "available", "message": f"Backup {snapshot_id} complete."}
            if state in ("failed", "error"):
                return {"status": "failed", "message": f"Backup {snapshot_id} failed: {state}."}
            await asyncio.sleep(POLL_INTERVAL)
            elapsed += POLL_INTERVAL
    return {"status": "timeout", "message": f"Backup {snapshot_id} not complete after {POLL_TIMEOUT}s."}


async def _webhook_poll(snapshot_id: str) -> dict:
    import httpx

    status_url = os.environ.get("BACKUP_WEBHOOK_STATUS_URL", os.environ["BACKUP_WEBHOOK_URL"])
    token      = os.getenv("BACKUP_WEBHOOK_TOKEN", "")
    elapsed    = 0
    async with httpx.AsyncClient() as client:
        while elapsed < POLL_TIMEOUT:
            resp = await client.get(
                f"{status_url}/{snapshot_id}",
                headers={"Authorization": f"Bearer {token}"} if token else {},
                timeout=30,
            )
            resp.raise_for_status()
            data  = resp.json()
            state = str(data.get("status", "")).lower()
            logger.info("Webhook backup %s status: %s (%ds elapsed)", snapshot_id, state, elapsed)
            if state in ("complete", "completed", "available", "success"):
                return {"status": "available", "message": f"Backup {snapshot_id} complete."}
            if state in ("failed", "error"):
                return {"status": "failed", "message": f"Backup {snapshot_id} failed."}
            await asyncio.sleep(POLL_INTERVAL)
            elapsed += POLL_INTERVAL
    return {"status": "timeout", "message": f"Backup {snapshot_id} not complete after {POLL_TIMEOUT}s."}


async def _aws_rds_snapshot(trace_id: str) -> dict:
    import boto3

    db_instance = os.environ["BACKUP_AWS_DB_INSTANCE_ID"]
    snapshot_id = f"migration-pre-{trace_id[:8]}"

    client = boto3.client(
        "rds",
        region_name=os.getenv("BACKUP_AWS_REGION", "us-west-2"),
        aws_access_key_id=os.getenv("BACKUP_AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("BACKUP_AWS_SECRET_ACCESS_KEY"),
    )
    response = client.create_db_snapshot(
        DBSnapshotIdentifier=snapshot_id,
        DBInstanceIdentifier=db_instance,
    )
    snap = response["DBSnapshot"]
    logger.info("AWS RDS snapshot triggered: %s (state=%s)", snapshot_id, snap["Status"])
    return {
        "provider":    "aws_rds",
        "snapshot_id": snap["DBSnapshotIdentifier"],
        "status":      "triggered",
        "metadata":    {"state": snap["Status"], "db_instance": db_instance},
    }


async def _supabase_backup(trace_id: str) -> dict:
    import httpx

    project_ref  = os.environ["BACKUP_SUPABASE_PROJECT_REF"]
    access_token = os.environ["BACKUP_SUPABASE_ACCESS_TOKEN"]

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"https://api.supabase.com/v1/projects/{project_ref}/database/backups",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()

    snapshot_id = str(data.get("id", ""))
    logger.info("Supabase backup triggered: project=%s snapshot=%s", project_ref, snapshot_id)
    return {
        "provider":    "supabase",
        "snapshot_id": snapshot_id,
        "status":      "triggered",
        "metadata":    data,
    }


async def _webhook_backup(source_url: str, trace_id: str) -> dict:
    import httpx

    webhook_url = os.environ["BACKUP_WEBHOOK_URL"]
    token       = os.getenv("BACKUP_WEBHOOK_TOKEN", "")

    parsed      = urllib.parse.urlparse(source_url)
    source_host = parsed.hostname or ""

    async with httpx.AsyncClient() as client:
        response = await client.post(
            webhook_url,
            json={"trace_id": trace_id, "source_host": source_host, "action": "snapshot"},
            headers={"Authorization": f"Bearer {token}"} if token else {},
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()

    snapshot_id = str(data.get("snapshot_id") or data.get("id") or "")
    logger.info("Webhook backup triggered: url=%s snapshot=%s", webhook_url, snapshot_id)
    return {
        "provider":    "webhook",
        "snapshot_id": snapshot_id,
        "status":      "triggered",
        "metadata":    data,
    }
