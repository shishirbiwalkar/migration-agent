"""
Shared mapping/promotion helpers used by the migration API and the agents.
Single source of truth for: SQL-identifier validation, JSON→Python coercion,
the promotion-config loader + default, and the field-name candidate lists.
"""

import re
import json
import uuid as uuid_mod
from datetime import datetime

_IDENT_RE = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_]*$')


def validate_identifier(name: str, context: str = "") -> str:
    """Reject any table/column name that isn't a safe SQL identifier."""
    if not _IDENT_RE.match(name or ""):
        raise ValueError(
            f"Unsafe SQL identifier from promotion_config"
            f"{' (' + context + ')' if context else ''}: {name!r}. "
            "Only alphanumeric and underscore characters are allowed."
        )
    return name


def coerce(value):
    """Convert JSON-serialized strings back to the Python types asyncpg expects."""
    if not isinstance(value, str):
        return value
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            pass
    try:
        return uuid_mod.UUID(value)
    except ValueError:
        pass
    return value


# ── Field-name candidates — agents may name columns differently ───────────────
NAME_FIELDS   = ("scientist_name", "name", "scientist", "user_name", "researcher_name")
WELL_FIELDS   = ("well_position", "experiment_well_position", "well", "position")
SIGNAL_FIELDS = ("signal", "experiment_signal", "raw_value", "value", "measurement")
ROLE_FIELDS   = ("scientist_role", "role", "user_role", "department", "dept")


def pick_field(data: dict, candidates, default=None):
    """Return the first present (non-None) value from candidate keys."""
    for c in candidates:
        v = data.get(c)
        if v is not None and v != "":
            return v
    return default


# ── Promotion config ──────────────────────────────────────────────────────────
DEFAULT_PROMOTION_CONFIG = {
    "staging_table": "gds_staging_experiments",
    "entity_table": {
        "name":       "gds_users",
        "column_map": {"scientist_name": "name", "scientist_role": "role"},
        "upsert_key": "name",
        "pk":         "gds_user_id",
    },
    "records_table": {
        "name":        "gds_experiments",
        "column_map":  {"well_position": "well_position", "signal": "signal"},
        "fk_column":   "gds_user_id",
        "upsert_keys": ["gds_user_id", "well_position"],
    },
}


async def load_promotion_config(conn, tid: uuid_mod.UUID) -> dict:
    """Load the agent's promotion config for a trace, or fall back to the default."""
    row = await conn.fetchrow(
        "SELECT plan_json FROM migration_plans WHERE trace_id=$1", tid)
    if row:
        return json.loads(row["plan_json"]) if isinstance(row["plan_json"], str) \
               else row["plan_json"]
    return DEFAULT_PROMOTION_CONFIG
