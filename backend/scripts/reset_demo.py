"""
Demo reset script — run this between demo sessions.

What it does:
  1. Clears ALL GDS tables (experiments, staging, users, audit, plans)
  2. Wipes ABASE and re-seeds 20 scientists + 80 wells from seed_abase_v2.sql

Usage:
  cd backend
  python scripts/reset_demo.py
"""
import asyncio
import os
import sys
import urllib.parse
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

ABASE_URL = os.getenv("ABASE_DATABASE_URL")
GDS_URL   = os.getenv("GDS_DATABASE_URL")

SEED_FILE = Path(__file__).parent.parent / "seed_abase_v2.sql"


def _parse(url: str) -> dict:
    p = urllib.parse.urlparse(url)
    return {
        "host":     p.hostname,
        "port":     p.port or 5432,
        "user":     urllib.parse.unquote(p.username or ""),
        "password": urllib.parse.unquote(p.password or ""),
        "database": p.path.lstrip("/"),
    }


async def reset_gds(conn: asyncpg.Connection) -> None:
    print("  Clearing GDS tables...")
    await conn.execute("""
        TRUNCATE
            migration_audit_log,
            migration_plans,
            gds_staging_experiments,
            gds_experiments,
            gds_users
        RESTART IDENTITY CASCADE
    """)
    print("  GDS cleared.")


async def reset_abase(conn: asyncpg.Connection) -> None:
    print("  Clearing ABASE...")
    await conn.execute("TRUNCATE experiments RESTART IDENTITY CASCADE")
    await conn.execute("TRUNCATE users RESTART IDENTITY CASCADE")

    seed_sql = SEED_FILE.read_text()

    # Skip the TRUNCATE lines — already done above — run only INSERT statements
    statements = [s.strip() for s in seed_sql.split(";") if s.strip()]
    insert_statements = [s for s in statements if "INSERT INTO" in s.upper()]

    for stmt in insert_statements:
        await conn.execute(stmt)

    user_count = await conn.fetchval("SELECT COUNT(*) FROM users")
    well_count = await conn.fetchval("SELECT COUNT(*) FROM experiments")
    print(f"  ABASE seeded: {user_count} scientists, {well_count} wells.")


async def main() -> None:
    if not ABASE_URL or not GDS_URL:
        print("ERROR: ABASE_DATABASE_URL or GDS_DATABASE_URL not set in .env")
        sys.exit(1)

    print("\n=== Demo Reset ===")

    print("\n[1/2] Resetting GDS...")
    gds_conn = await asyncpg.connect(**_parse(GDS_URL), ssl="require", statement_cache_size=0)
    try:
        await reset_gds(gds_conn)
    finally:
        await gds_conn.close()

    print("\n[2/2] Resetting ABASE...")
    abase_conn = await asyncpg.connect(**_parse(ABASE_URL), ssl="require", statement_cache_size=0)
    try:
        await reset_abase(abase_conn)
    finally:
        await abase_conn.close()

    print("\nDone. Ready for next demo run.\n")


if __name__ == "__main__":
    asyncio.run(main())
