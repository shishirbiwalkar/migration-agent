"""
Local SQLite backup log — stores backup metadata before each migration.
Auto-creates the DB file and table on first use. No setup required.
File location: backend/data/backup_log.db
"""

import sqlite3
import asyncio
import json
import os
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_DB_PATH = Path(__file__).parent.parent.parent / "data" / "backup_log.db"


def _get_conn() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS backup_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            trace_id    TEXT    NOT NULL UNIQUE,
            source_host TEXT    NOT NULL,
            provider    TEXT    NOT NULL,
            snapshot_id TEXT,
            status      TEXT    NOT NULL DEFAULT 'triggered',
            metadata    TEXT,
            created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    return conn


def _save(trace_id: str, source_host: str, provider: str,
          snapshot_id: str | None, status: str, metadata: dict) -> None:
    conn = _get_conn()
    try:
        conn.execute("""
            INSERT OR IGNORE INTO backup_log
                (trace_id, source_host, provider, snapshot_id, status, metadata)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (trace_id, source_host, provider, snapshot_id, status, json.dumps(metadata)))
        conn.commit()
    finally:
        conn.close()


def _update_status(trace_id: str, status: str) -> None:
    conn = _get_conn()
    try:
        conn.execute("UPDATE backup_log SET status=? WHERE trace_id=?", (status, trace_id))
        conn.commit()
    finally:
        conn.close()


# Async wrappers — run sqlite (blocking) in a thread so we don't block the event loop

async def save_backup(trace_id: str, source_host: str, provider: str,
                      snapshot_id: str | None, status: str, metadata: dict) -> None:
    await asyncio.to_thread(_save, trace_id, source_host, provider, snapshot_id, status, metadata)
    logger.info("Backup log saved: trace=%s provider=%s status=%s", trace_id, provider, status)


async def update_backup_status(trace_id: str, status: str) -> None:
    await asyncio.to_thread(_update_status, trace_id, status)
