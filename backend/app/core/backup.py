"""
Backup Orchestrator
====================
Triggers a backup of the source database BEFORE migration starts.
The agent calls trigger_backup as its very first step and will not
proceed unless the backup is confirmed.

Supported providers (set BACKUP_PROVIDER env var):
  pg_dump   — Real SQL dump via pg_dump saved to backend/data/backups/ (DEFAULT)
  aws_rds   — AWS RDS CreateDBSnapshot via boto3
  supabase  — Supabase Management API
  webhook   — Generic HTTP webhook (enterprise backup managers, Oracle, etc.)
  none      — No backup (development only; logs a warning)
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
    Trigger a backup via the configured provider.
    Returns {"provider", "snapshot_id", "status", "metadata"}.
    Raises RuntimeError on failure — caller decides whether to abort the migration.
    """
    if PROVIDER == "pg_dump":
        return await _pg_dump_backup(source_url, trace_id)
    elif PROVIDER == "aws_rds":
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
                           "Valid values: pg_dump, aws_rds, supabase, webhook, none.")


async def _pg_dump_backup(source_url: str, trace_id: str) -> dict:
    """
    Pure-Python SQL backup using asyncpg — no pg_dump binary required.
    Connects to the source database, reads every table's schema and rows,
    and writes a plain-SQL restore file to backend/data/backups/{trace_id}.sql.
    The agent waits for the dump to complete before proceeding.
    """
    import asyncpg
    import gzip
    import urllib.parse
    from pathlib import Path
    from datetime import datetime, timezone

    backup_dir = Path(__file__).parent.parent.parent / "data" / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    dump_file = backup_dir / f"{trace_id}.sql.gz"

    p        = urllib.parse.urlparse(source_url)
    host     = p.hostname or ""
    port     = p.port or 5432
    user     = urllib.parse.unquote(p.username or "")
    password = urllib.parse.unquote(p.password or "")
    dbname   = p.path.lstrip("/")

    logger.info("SQL backup starting: host=%s db=%s → %s", host, dbname, dump_file.name)

    conn = await asyncpg.connect(
        host=host, port=port, user=user, password=password,
        database=dbname, ssl="require", statement_cache_size=0,
    )

    try:
        tables = await conn.fetch("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
            ORDER BY table_name
        """)

        lines = [
            f"-- Migration Agent SQL Backup",
            f"-- Source: {host}/{dbname}",
            f"-- trace_id: {trace_id}",
            f"-- Generated: {datetime.now(timezone.utc).isoformat()}",
            f"-- Restore: psql <target_url> < {dump_file.stem}",
            "",
        ]

        total_rows = 0
        for t in tables:
            tname = t["table_name"]

            # Column names for this table
            cols = await conn.fetch("""
                SELECT column_name, data_type
                FROM information_schema.columns
                WHERE table_schema='public' AND table_name=$1
                ORDER BY ordinal_position
            """, tname)
            col_names = [c["column_name"] for c in cols]
            if not col_names:
                continue

            rows = await conn.fetch(f'SELECT * FROM "{tname}"')
            if not rows:
                lines.append(f"-- Table {tname}: 0 rows")
                continue

            lines.append(f"-- Table: {tname} ({len(rows)} rows)")
            col_list = ", ".join(f'"{c}"' for c in col_names)

            for row in rows:
                vals = []
                for c in col_names:
                    v = row[c]
                    if v is None:
                        vals.append("NULL")
                    elif isinstance(v, bool):
                        vals.append("TRUE" if v else "FALSE")
                    elif isinstance(v, (int, float)):
                        vals.append(str(v))
                    else:
                        # Escape single quotes, wrap in quotes
                        vals.append("'" + str(v).replace("'", "''") + "'")
                val_list = ", ".join(vals)
                lines.append(f'INSERT INTO "{tname}" ({col_list}) VALUES ({val_list});')

            total_rows += len(rows)
            lines.append("")

    finally:
        await conn.close()

    sql_content = "\n".join(lines)
    with gzip.open(dump_file, "wt", encoding="utf-8") as f:
        f.write(sql_content)

    size_kb = round(dump_file.stat().st_size / 1024, 1)
    logger.info("SQL backup complete: %s (%.1f KB, %d rows)", dump_file.name, size_kb, total_rows)

    return {
        "provider":    "pg_dump",
        "snapshot_id": str(dump_file),
        "status":      "available",
        "metadata":    {
            "file":       str(dump_file),
            "size_kb":    size_kb,
            "total_rows": total_rows,
            "host":       host,
            "db":         dbname,
            "restore":    f"gunzip -c {dump_file.name} | psql <connection_url>",
        },
    }


async def check_backup_status(snapshot_id: str, provider: str) -> dict:
    """
    Poll the backup provider until the snapshot is complete, failed, or timed out.
    Returns {"status": "available" | "failed" | "timeout", "message": ...}
    """
    if provider == "pg_dump":
        # pg_dump is synchronous — if trigger_backup returned, the file exists
        from pathlib import Path
        f = Path(snapshot_id)
        if f.exists() and f.stat().st_size > 0:
            return {"status": "available",
                    "message": f"SQL dump ready: {f.name} ({round(f.stat().st_size/1024,1)} KB)",
                    "confirmed": True}
        return {"status": "failed", "message": f"Dump file not found: {snapshot_id}"}
    elif provider == "aws_rds":
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
