import os
import asyncpg
import urllib.parse
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[3] / ".env")

_pool: asyncpg.Pool | None = None


def _parse_dsn() -> dict:
    raw = os.environ["ABASE_DATABASE_URL"]
    p = urllib.parse.urlparse(raw)
    return {
        "host":     p.hostname,
        "port":     p.port or 5432,
        "user":     urllib.parse.unquote(p.username or ""),
        "password": urllib.parse.unquote(p.password or ""),
        "database": p.path.lstrip("/"),
    }


async def get_abase_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            **_parse_dsn(),
            ssl="require",
            min_size=2,
            max_size=10,
            command_timeout=30,
            statement_cache_size=0,
        )
    return _pool


async def close_abase_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
