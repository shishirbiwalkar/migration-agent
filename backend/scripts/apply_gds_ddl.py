"""
One-off DDL migration — add dose-response columns to gds_experiments.

Run once after updating schema_gds.sql:
  cd backend
  python scripts/apply_gds_ddl.py
"""
import asyncio
import os
import urllib.parse
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")
GDS_URL = os.getenv("GDS_DATABASE_URL")

DDL_STATEMENTS = [
    ("make well_position nullable",
     "ALTER TABLE gds_experiments ALTER COLUMN well_position DROP NOT NULL"),
    ("drop old well-based unique constraint",
     "ALTER TABLE gds_experiments DROP CONSTRAINT IF EXISTS uq_experiments_user_well"),
    ("add compound-based unique constraint",
     "ALTER TABLE gds_experiments ADD CONSTRAINT uq_experiments_user_compound "
     "UNIQUE (gds_user_id, compound_id)"),
    ("add ec50_um column",
     "ALTER TABLE gds_experiments ADD COLUMN IF NOT EXISTS ec50_um DOUBLE PRECISION"),
    ("add hill_slope column",
     "ALTER TABLE gds_experiments ADD COLUMN IF NOT EXISTS hill_slope DOUBLE PRECISION"),
    ("add r_squared column",
     "ALTER TABLE gds_experiments ADD COLUMN IF NOT EXISTS r_squared DOUBLE PRECISION"),
    ("add curve_quality column",
     "ALTER TABLE gds_experiments ADD COLUMN IF NOT EXISTS curve_quality TEXT"),
    ("add num_concentration_points column",
     "ALTER TABLE gds_experiments ADD COLUMN IF NOT EXISTS num_concentration_points INTEGER"),
    ("add curve_data column",
     "ALTER TABLE gds_experiments ADD COLUMN IF NOT EXISTS curve_data JSONB"),
    ("add plate_barcode column",
     "ALTER TABLE gds_experiments ADD COLUMN IF NOT EXISTS plate_barcode TEXT"),
]


def _parse(url: str) -> dict:
    p = urllib.parse.urlparse(url)
    return {
        "host":     p.hostname,
        "port":     p.port or 5432,
        "user":     urllib.parse.unquote(p.username or ""),
        "password": urllib.parse.unquote(p.password or ""),
        "database": p.path.lstrip("/"),
    }


async def main() -> None:
    if not GDS_URL:
        print("ERROR: GDS_DATABASE_URL not set in .env")
        return

    print("\n=== GDS Schema Migration ===\n")
    conn = await asyncpg.connect(**_parse(GDS_URL), ssl="require", statement_cache_size=0)
    try:
        async with conn.transaction():
            for label, sql in DDL_STATEMENTS:
                await conn.execute(sql)
                print(f"  ✓ {label}")

        # Verify columns exist
        cols = await conn.fetch("""
            SELECT column_name, data_type, is_nullable
            FROM   information_schema.columns
            WHERE  table_schema = 'public' AND table_name = 'gds_experiments'
            ORDER  BY ordinal_position
        """)
        new_cols = {"ec50_um", "hill_slope", "r_squared", "curve_quality", "num_concentration_points"}
        found = {r["column_name"] for r in cols}

        print("\n── Column verification ──────────────────────────")
        for c in new_cols:
            status = "✓" if c in found else "✗ MISSING"
            print(f"  {status}  {c}")

        # Verify constraint
        constraints = await conn.fetch("""
            SELECT conname FROM pg_constraint c
            JOIN   pg_class t ON t.oid = c.conrelid
            WHERE  t.relname = 'gds_experiments' AND c.contype = 'u'
        """)
        cnames = {r["conname"] for r in constraints}
        print("\n── Constraint verification ──────────────────────")
        print(f"  {'✓' if 'uq_experiments_user_compound' in cnames else '✗'}"
              "  uq_experiments_user_compound (new)")
        print(f"  {'✗ still present!' if 'uq_experiments_user_well' in cnames else '✓ removed'}"
              "  uq_experiments_user_well (old)")

        well_nullable = next(
            (r["is_nullable"] for r in cols if r["column_name"] == "well_position"), None)
        print(f"\n── well_position nullable: {well_nullable} (should be YES) ──")

        print("\n  Status: DONE ✓\n")

    except Exception as e:
        print(f"\n  ERROR: {e}")
        raise
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
