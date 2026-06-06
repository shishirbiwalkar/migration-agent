"""
Demo reset script — run this before EVERY demo session.

What it does:
  1. Clears ALL GDS tables (experiments, staging, users, audit, plans)
  2. Wipes ABASE and re-seeds 20 scientists + 160 wells from seed_abase_v2.sql
  3. Verifies final counts so you can confirm state is clean before starting

Usage:
  cd backend
  python scripts/reset_demo.py

Expected output:
  ABASE → 20 scientists, 160 wells  (8-point dose-response, 10 BAD curves flagged)
  GDS   → 0 users, 0 experiments, 0 staging rows
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
            migration_jobs,
            migration_mappings,
            gds_staging_experiments,
            gds_experiments,
            gds_users
        RESTART IDENTITY CASCADE
    """)
    # Verify the truncate actually took effect
    remaining = await conn.fetchval("SELECT COUNT(*) FROM gds_users")
    if remaining != 0:
        raise RuntimeError(f"GDS TRUNCATE failed — gds_users still has {remaining} rows.")
    print("  GDS cleared. ✓")


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

    if user_count != 20 or well_count != 240:
        raise RuntimeError(
            f"ABASE seed failed — expected 20 scientists / 240 wells, "
            f"got {user_count} / {well_count}."
        )
    print(f"  ABASE seeded: {user_count} scientists, {well_count} wells. ✓")


async def verify(gds_conn: asyncpg.Connection, abase_conn: asyncpg.Connection) -> None:
    abase_users = await abase_conn.fetchval("SELECT COUNT(*) FROM users")
    abase_wells = await abase_conn.fetchval("SELECT COUNT(*) FROM experiments")
    gds_users   = await gds_conn.fetchval("SELECT COUNT(*) FROM gds_users")
    gds_exps    = await gds_conn.fetchval("SELECT COUNT(*) FROM gds_experiments")
    staging     = await gds_conn.fetchval("SELECT COUNT(*) FROM gds_staging_experiments")

    print("\n── Final State ─────────────────────────────")
    print(f"  ABASE → {abase_users} scientists, {abase_wells} wells")
    print(f"  GDS   → {gds_users} users, {gds_exps} experiments, {staging} staging rows")

    ok = (abase_users == 20 and abase_wells == 240
          and gds_users == 0 and gds_exps == 0 and staging == 0)
    if ok:
        print("  Status: READY FOR DEMO ✓")
    else:
        print("  Status: WARNING — unexpected counts, do not start demo.")
        sys.exit(1)
    print("────────────────────────────────────────────\n")


async def main() -> None:
    if not ABASE_URL or not GDS_URL:
        print("ERROR: ABASE_DATABASE_URL or GDS_DATABASE_URL not set in .env")
        sys.exit(1)

    print("\n=== Demo Reset ===")

    print("\n[1/2] Resetting GDS...")
    gds_conn = await asyncpg.connect(**_parse(GDS_URL), ssl="require", statement_cache_size=0)
    try:
        await reset_gds(gds_conn)
    except Exception as e:
        print(f"  ERROR: {e}")
        sys.exit(1)
    finally:
        await gds_conn.close()

    print("\n[2/2] Resetting ABASE...")
    abase_conn = await asyncpg.connect(**_parse(ABASE_URL), ssl="require", statement_cache_size=0)
    try:
        await reset_abase(abase_conn)
    except Exception as e:
        print(f"  ERROR: {e}")
        sys.exit(1)
    finally:
        await abase_conn.close()

    # Final verification using fresh connections
    gds_conn   = await asyncpg.connect(**_parse(GDS_URL),   ssl="require", statement_cache_size=0)
    abase_conn = await asyncpg.connect(**_parse(ABASE_URL), ssl="require", statement_cache_size=0)
    try:
        await verify(gds_conn, abase_conn)
    finally:
        await gds_conn.close()
        await abase_conn.close()


if __name__ == "__main__":
    asyncio.run(main())
