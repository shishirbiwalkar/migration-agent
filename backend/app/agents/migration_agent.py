"""
Migration AI Agent — Generic, Read-Only
=========================================
The agent is PURELY COMPUTATIONAL. It never writes to any database.

What the agent does:
  1. Reads source DB schema via information_schema
  2. Reads target DB schema via information_schema
  3. Samples real rows from source tables
  4. Writes + self-repairs a pandas transformation script
  5. Classifies each row by risk (statistical anomaly detection)
  6. Produces a promotion config (how staging maps to target tables)
  7. Returns cleaned records + config to the caller

What the agent does NOT do:
  - Write to any database
  - Execute SQL
  - Approve or reject records
  - Make production changes

The calling layer (api/agent.py) handles all DB writes.
This separation means the agent works for ANY source → target migration:
pass any source_url + target_url and it figures out the mapping.

Tools (all READ-ONLY or IN-MEMORY):
  1. discover_source_schema   — reads information_schema from source DB
  2. discover_target_schema   — reads information_schema from target DB
  3. sample_source_data       — fetches rows from source (read-only)
  4. write_and_test_mapping   — transforms data in-memory, no DB writes
  5. store_promotion_config   — stores mapping config in agent state (no DB)
"""

import os
import uuid
import json
import asyncpg
import logging
import traceback
import urllib.parse
import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd
from google.genai import types

from app.core.llm import get_client, generate_with_backoff, MODEL as _MODEL

logger    = logging.getLogger(__name__)
MAX_TURNS = 15

# ── Dynamic pool creation (no hardcoded ABASE/GDS) ───────────────────────────

async def _create_pool(url: str) -> asyncpg.Pool:
    p = urllib.parse.urlparse(url)
    return await asyncpg.create_pool(
        host=urllib.parse.unquote(p.hostname or ""),
        port=p.port or 5432,
        user=urllib.parse.unquote(p.username or ""),
        password=urllib.parse.unquote(p.password or ""),
        database=p.path.lstrip("/"),
        ssl="require",
        min_size=1,
        max_size=5,
        command_timeout=30,
        statement_cache_size=0,
    )


# ── Per-run state — no module-level globals ───────────────────────────────────

@dataclass
class _RunState:
    source_pool:       asyncpg.Pool | None            = None
    target_pool:       asyncpg.Pool | None            = None
    source_tables:     dict[str, pd.DataFrame]        = field(default_factory=dict)
    cleaned_df:        pd.DataFrame | None            = None
    promotion_config:  dict                           = field(default_factory=dict)
    transform_script:  str | None                     = None   # captured for cache/replay


# ── Shared schema reader ──────────────────────────────────────────────────────

async def _read_schema(pool: asyncpg.Pool) -> dict:
    async with pool.acquire() as conn:
        table_rows = await conn.fetch("""
            SELECT table_name FROM information_schema.tables
            WHERE  table_schema = 'public' AND table_type = 'BASE TABLE'
            ORDER  BY table_name
        """)
        schema: dict[str, Any] = {}
        for t in table_rows:
            tname = t["table_name"]
            cols  = await conn.fetch("""
                SELECT column_name, data_type, is_nullable, column_default
                FROM   information_schema.columns
                WHERE  table_schema = 'public' AND table_name = $1
                ORDER  BY ordinal_position
            """, tname)
            count = await conn.fetchval(f'SELECT COUNT(*) FROM "{tname}"')
            schema[tname] = {
                "columns": [
                    {"name": c["column_name"], "type": c["data_type"],
                     "nullable": c["is_nullable"],
                     "default": str(c["column_default"]) if c["column_default"] else None}
                    for c in cols
                ],
                "row_count": count,
            }
        fks = await conn.fetch("""
            SELECT kcu.table_name  AS from_table,
                   kcu.column_name AS from_column,
                   ccu.table_name  AS to_table,
                   ccu.column_name AS to_column
            FROM information_schema.table_constraints     tc
            JOIN information_schema.key_column_usage      kcu
                ON tc.constraint_name = kcu.constraint_name
               AND tc.table_schema    = kcu.table_schema
            JOIN information_schema.constraint_column_usage ccu
                ON ccu.constraint_name = tc.constraint_name
               AND ccu.table_schema    = tc.table_schema
            WHERE tc.constraint_type = 'FOREIGN KEY'
              AND tc.table_schema    = 'public'
        """)
    return {"tables": schema, "foreign_keys": [dict(r) for r in fks]}


# ── Tool 1: discover_source_schema (READ ONLY) ────────────────────────────────

async def _tool_discover_source_schema(state: _RunState) -> dict:
    result = await _read_schema(state.source_pool)
    return {
        "status":   "success",
        "database": "SOURCE",
        **result,
        "note": "Read-only inspection of source. Discover target schema next.",
    }


# ── Tool 2: discover_target_schema (READ ONLY) ────────────────────────────────

async def _tool_discover_target_schema(state: _RunState) -> dict:
    result = await _read_schema(state.target_pool)
    return {
        "status":   "success",
        "database": "TARGET",
        **result,
        "note": (
            "Identify three things: "
            "(1) The staging buffer table — it has a 'data JSONB' column and a 'risk_level' column. "
            "(2) The production entity table — stores unique entities (users, customers, etc). "
            "(3) The production records table — stores individual records with a FK to the entity table. "
            "IGNORE infrastructure tables (audit logs, migration plans, config tables). "
            "The staging table uses JSONB — your mapping script can use any column names. "
            "Your promotion_config must describe how your staging columns map to the production tables."
        ),
    }


# ── Tool 3: sample_source_data (READ ONLY) ───────────────────────────────────

async def _tool_sample_source_data(
    table_name: str, limit: int, state: _RunState
) -> dict:
    limit = min(max(limit, 1), 20)
    async with state.source_pool.acquire() as conn:
        exists = await conn.fetchval("""
            SELECT COUNT(*) FROM information_schema.tables
            WHERE  table_schema='public' AND table_name=$1
        """, table_name)
        if not exists:
            valid = await conn.fetch(
                "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
            )
            valid_names = [r["table_name"] for r in valid]
            return {"status": "error",
                    "error": f"Table '{table_name}' not found. "
                             f"Valid source tables: {valid_names}. "
                             f"Pass a single table name string, e.g. table_name='users'."}
        rows = await conn.fetch(f'SELECT * FROM "{table_name}" LIMIT $1', limit)

    def _safe(v: Any) -> Any:
        if isinstance(v, uuid.UUID):   return str(v)
        if hasattr(v, "isoformat"):    return v.isoformat()
        return v

    return {
        "status": "success", "table": table_name,
        "rows":   [{k: _safe(v) for k, v in dict(r).items()} for r in rows],
        "count":  len(rows),
    }


# ── Tool 4: write_and_test_mapping (IN-MEMORY, no DB writes) ─────────────────

def _safe_cell(v: Any) -> Any:
    """JSON/DataFrame-safe scalar coercion for values read from the source DB."""
    if isinstance(v, uuid.UUID): return str(v)
    if hasattr(v, "isoformat"):  return v.isoformat()
    return v


async def _load_source_tables(source_pool: asyncpg.Pool) -> dict[str, pd.DataFrame]:
    """Read every source table fully into memory as DataFrames (READ ONLY)."""
    tables: dict[str, pd.DataFrame] = {}
    async with source_pool.acquire() as conn:
        tnames = await conn.fetch("""
            SELECT table_name FROM information_schema.tables
            WHERE  table_schema='public' AND table_type='BASE TABLE'
        """)
    for t in tnames:
        async with source_pool.acquire() as conn:
            rows = await conn.fetch(f'SELECT * FROM "{t["table_name"]}"')
        tables[t["table_name"]] = pd.DataFrame(
            [{k: _safe_cell(v) for k, v in dict(r).items()} for r in rows]
        )
    return tables


def _execute_pandas_transform(source_tables: dict[str, pd.DataFrame], script: str) -> pd.DataFrame:
    """
    Execute a pandas transform script against source tables, in-memory, no DB connection.
    Shared by the agent's write_and_test tool and the deterministic cache-replay path.
    Raises ValueError on any contract violation; the caller decides how to surface it.
    """
    namespace = {name: df.copy() for name, df in source_tables.items()}
    namespace["pd"] = pd
    namespace["np"] = np   # for statistical threshold computation

    exec(script, namespace)  # noqa: S102

    result = namespace.get("result")
    if result is None:
        raise ValueError("Script must assign the output to a variable named `result`.")
    if not isinstance(result, pd.DataFrame):
        raise ValueError(f"`result` must be a DataFrame, got {type(result).__name__}.")
    if result.empty:
        raise ValueError("`result` is empty. Check JOIN/filter logic.")

    if "risk_level" not in result.columns:
        result["risk_level"] = "auto"

    before = len(result)
    result = result.dropna(subset=[c for c in result.columns if c != "risk_level"]).copy()
    dropped = before - len(result)
    if dropped:
        logger.warning("dropna removed %d rows — nullable columns likely included in result.", dropped)
    return result


async def _tool_write_and_test_mapping(
    script: str, state: _RunState
) -> dict:
    # Load all source tables into memory once (READ ONLY from source DB)
    if not state.source_tables:
        state.source_tables = await _load_source_tables(state.source_pool)

    try:
        result = _execute_pandas_transform(state.source_tables, script)

        state.cleaned_df = result
        state.transform_script = script   # capture for the reusable mapping cache

        # Anomaly summary
        anomalies = result[result["risk_level"] == "review"].shape[0]
        auto      = result[result["risk_level"] == "auto"].shape[0]

        # Signal stats if present
        sig_stats = {}
        if "signal" in result.columns:
            s = result["signal"].astype(float)
            sig_stats = {
                "mean":  round(float(s.mean()), 4),
                "std":   round(float(s.std()),  4),
                "min":   round(float(s.min()),  4),
                "max":   round(float(s.max()),  4),
            }

        return {
            "status":              "success",
            "rows_after_cleaning": len(result),
            "columns":             list(result.columns),
            "auto_rows":           auto,
            "review_rows":         anomalies,
            "signal_stats":        sig_stats,
            "sample":              [
                {k: (str(v) if isinstance(v, uuid.UUID) else
                     v.isoformat() if hasattr(v, "isoformat") else v)
                 for k, v in row.items()}
                for row in result.head(3).to_dict(orient="records")
            ],
            "note": (
                "Data transformed in-memory. No database writes performed. "
                "Call store_promotion_config next, then the API layer will handle staging."
            ),
        }

    except Exception as exc:
        return {
            "status": "error",
            "error":  f"{type(exc).__name__}: {exc}",
            "hint":   "Fix the script and call write_and_test_mapping again.",
        }


# ── Tool 5: store_promotion_config (IN-MEMORY, no DB writes) ─────────────────

async def _tool_store_promotion_config(
    config_json: str, state: _RunState
) -> dict:
    """
    Agent stores the promotion config in run state.
    This config tells the infrastructure how to promote staging rows
    to the final production tables — without the agent touching the DB.
    """
    try:
        config = json.loads(config_json)
        state.promotion_config = config
        return {
            "status": "success",
            "config": config,
            "note": (
                "Config stored in agent state. The API layer will use this to "
                "write data to staging and promote to production. "
                "Your job is done — return your summary."
            ),
        }
    except json.JSONDecodeError as e:
        return {"status": "error", "error": f"Invalid JSON: {e}"}


# ── System prompt ─────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are an autonomous data migration agent.

IMPORTANT: You are READ-ONLY. You never write to any database.
Your job is to UNDERSTAND and TRANSFORM — the infrastructure handles all writes.

MANDATORY WORKFLOW — you MUST complete ALL 5 steps in order before stopping:
1. discover_source_schema  → understand source tables, columns, FKs, data types
2. discover_target_schema  → understand target production tables and the staging buffer
3. sample_source_data      → see real data values to understand meaning of each column
4. write_and_test_mapping  → semantically map source → target, transform in-memory
5. store_promotion_config  → record how your intermediate columns map to target tables

You CANNOT stop or say you are done until write_and_test_mapping AND store_promotion_config have both been called successfully. Discovering schemas and sampling data is NOT enough — you must complete the transformation and store the config.

CORE RESPONSIBILITY — SEMANTIC COLUMN MAPPING:
Source and target databases will have DIFFERENT column names for the same concept.
Your job is to reason about what each column means and map it correctly.
Example: source has 'raw_value', target has 'signal' — these are the same thing.
Example: source has 'dept', target has 'department' — same thing, different name.
Never assume column names match. Always reason from schema + sampled data.

MAPPING SCRIPT RULES (write_and_test_mapping):
- Available: one DataFrame per source table (named after the table), pd, np
- Output must be a DataFrame assigned to `result`
- Column names in `result` are YOUR intermediate names — choose them to be clear
- Join source tables as needed using FK relationships you discovered
- INFRASTRUCTURE METADATA (separate from data columns):
  * source_experiment_id: ALWAYS include the source primary key for traceability
    Example: result['source_experiment_id'] = experiments['id']
    This goes in column_map, but MUST NOT be in upsert_keys (it's infrastructure, not a constraint column)
  * risk_level: 'review' for anomalous rows, 'auto' for clean rows (infrastructure, not mapped)
  * trace_id, approved_by: added by infrastructure, never include in your result
- CRITICAL: Select ONLY the columns you will map to target production tables.
  Do not include nullable source columns you don't need — the infrastructure drops
  rows that have any null value, causing silent data loss.

ANOMALY DETECTION (statistical — not hardcoded):
- Identify the primary numeric measurement column from your schema analysis
- Apply mean ± 2σ threshold on that column
  mean = result['<numeric_col>'].mean()
  std  = result['<numeric_col>'].std()
  result['risk_level'] = result['<numeric_col>'].apply(
      lambda v: 'review' if v < (mean - 2*std) or v > (mean + 2*std) else 'auto'
  )

PROMOTION CONFIG (store_promotion_config):
After successful mapping, store how your `result` columns map to the target tables.

CRITICAL rules:
- column_map keys must be the EXACT column names you used in `result` (source/staging side).
- column_map values must be the EXACT column names in the TARGET table (not staging names).
- upsert_key must be the TARGET TABLE column name used in the UNIQUE constraint (e.g. "name", NOT your staging column like "user_name").
- upsert_keys must be ONLY TARGET TABLE column names that form the actual UNIQUE constraint in the target table (e.g. ["gds_user_id", "well_position"]). Do NOT include infrastructure columns like source_experiment_id, trace_id, or approved_by.
- You MAY include source_experiment_id in column_map (for traceability), but MUST NOT include it in upsert_keys — it's infrastructure metadata, not a constraint column.
- Do NOT include "trace_id" or "approved_by" in any column_map — those are added by infrastructure.
- The infrastructure reads JSONB staging data by column_map keys — a mismatch causes failure.

{
  "staging_table": "<staging table name you discovered>",
  "entity_table": {
    "name": "<target entity/users table name>",
    "column_map": {"<your_result_col>": "<target_table_col>", ...},
    "upsert_key": "<TARGET table column name for ON CONFLICT — e.g. 'name'>",
    "pk": "<primary key column in target entity table>"
  },
  "records_table": {
    "name": "<target records table name>",
    "column_map": {
      "signal": "signal",
      "user_id": "gds_user_id",
      "well_position": "well_position",
      "source_experiment_id": "source_experiment_id"
    },
    "fk_column": "<FK column name in target records table>",
    "upsert_keys": ["gds_user_id", "well_position"]
  },
  "anomaly_thresholds": {
    "<your_result_col>": {"low": <float>, "high": <float>, "method": "mean_2sigma"}
  }
}

NOTE: In the example above:
- upsert_keys is ["gds_user_id", "well_position"] — ONLY the columns in the actual UNIQUE constraint
- column_map INCLUDES source_experiment_id for traceability, but it's NOT in upsert_keys
- This works because the target table has a UNIQUE(gds_user_id, well_position) constraint
- Do NOT include infrastructure columns (source_experiment_id, trace_id, approved_by) in upsert_keys

After store_promotion_config succeeds, provide a final summary and stop.
Do not attempt to write to any database — the infrastructure handles that."""


# ── Tool definitions (Gemini format) ─────────────────────────────────────────

TOOL_DEFINITIONS = types.Tool(function_declarations=[

    types.FunctionDeclaration(
        name="discover_source_schema",
        description=(
            "READ-ONLY. Inspect the source database via information_schema. "
            "Returns tables, columns, data types, FK relationships. Call FIRST."
        ),
        parameters=types.Schema(type=types.Type.OBJECT, properties={}),
    ),

    types.FunctionDeclaration(
        name="discover_target_schema",
        description=(
            "READ-ONLY. Inspect the target database via information_schema. "
            "Find the staging table (has status/trace_id columns) and production tables. "
            "Call SECOND — before writing any mapping."
        ),
        parameters=types.Schema(type=types.Type.OBJECT, properties={}),
    ),

    types.FunctionDeclaration(
        name="sample_source_data",
        description=(
            "READ-ONLY. Fetch sample rows from any source table. "
            "Use to confirm column contents and data shape."
        ),
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "table_name": types.Schema(type=types.Type.STRING,
                                           description="Source table to sample"),
                "limit":      types.Schema(type=types.Type.INTEGER,
                                           description="Rows to return (1-20, default 10)"),
            },
            required=["table_name"],
        ),
    ),

    types.FunctionDeclaration(
        name="write_and_test_mapping",
        description=(
            "IN-MEMORY ONLY. No database writes. "
            "Execute a pandas script to transform source data into staging schema. "
            "Available: source table DataFrames (named after tables), pd, np. "
            "Assign output to `result`. Include risk_level column using statistical thresholds. "
            "On error, read the traceback and retry with a fixed script."
        ),
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "script": types.Schema(type=types.Type.STRING,
                                       description="Python script. No DB access. Sets `result`."),
            },
            required=["script"],
        ),
    ),

    types.FunctionDeclaration(
        name="store_promotion_config",
        description=(
            "IN-MEMORY ONLY. No database writes. "
            "Store the promotion config describing how staging → production tables. "
            "Call after write_and_test_mapping succeeds. "
            "The infrastructure uses this config to safely promote data."
        ),
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "config_json": types.Schema(
                    type=types.Type.STRING,
                    description="JSON string with entity_table, records_table, anomaly_thresholds",
                ),
            },
            required=["config_json"],
        ),
    ),
])


# ── Agent loop ────────────────────────────────────────────────────────────────

async def run_migration_agent(
    trace_id:   str,
    source_url: str | None = None,
    target_url: str | None = None,
) -> dict:
    """
    Pure computation — no DB writes.
    Returns cleaned records + promotion config for the caller to act on.
    """
    source_url = source_url or os.environ["ABASE_DATABASE_URL"]
    target_url = target_url or os.environ["GDS_DATABASE_URL"]

    client = get_client()
    state  = _RunState()
    log    = logging.LoggerAdapter(logger, {"trace_id": trace_id})
    trace: list[dict[str, Any]] = []

    def _step(action: str, **kwargs):
        entry = {"action": action, "timestamp": datetime.utcnow().isoformat(),
                 **{k: str(v)[:300] for k, v in kwargs.items()}}
        trace.append(entry)
        log.info("AGENT [%s] %s", action, str(kwargs)[:120])

    # Create dynamic pools — both created before try/finally so both are closed on failure
    state.source_pool = await _create_pool(source_url)
    try:
        state.target_pool = await _create_pool(target_url)
    except Exception:
        await state.source_pool.close()
        raise

    _step("AGENT_START",
          message="Agent started. Read-only mode. Will discover schemas, transform data, produce config.",
          source=source_url.split("@")[-1],
          target=target_url.split("@")[-1])

    contents: list[types.Content] = [
        types.Content(role="user", parts=[
            types.Part(text=(
                f"Migrate all records from the SOURCE database to the TARGET database. "
                f"trace_id: {trace_id}. "
                f"Discover both schemas, transform the data, classify anomalies statistically, "
                f"and store the promotion config. Do not write to any database."
            ))
        ])
    ]

    turns_used = 0

    _cfg = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        tools=[TOOL_DEFINITIONS],
        temperature=0.1,
    )

    tool_failures: dict[str, int] = {}
    MAX_TOOL_FAILURES = 5      # abort if any single tool errors this many times total
    aborted_tool: str | None = None

    try:
        for turn in range(MAX_TURNS):
            turns_used = turn + 1

            response = await generate_with_backoff(client, contents=contents, config=_cfg, model=_MODEL)

            if not response.candidates:
                _step("NO_CANDIDATES",
                      message="Gemini returned no candidates — safety filter or quota. Stopping.")
                break

            candidate = response.candidates[0]
            if not candidate.content or not candidate.content.parts:
                _step("EMPTY_CONTENT",
                      message=f"Gemini candidate has no content. finish_reason={candidate.finish_reason}. Stopping.")
                break

            contents.append(types.Content(role="model", parts=candidate.content.parts))

            tool_calls = [p for p in candidate.content.parts if p.function_call]

            if not tool_calls:
                # Guard: agent cannot stop until mapping and config are done
                if state.cleaned_df is None or not state.promotion_config:
                    missing = []
                    if state.cleaned_df is None:
                        missing.append("write_and_test_mapping")
                    if not state.promotion_config:
                        missing.append("store_promotion_config")
                    reminder = (
                        f"You stopped early. You have NOT completed the mandatory workflow. "
                        f"You must still call: {', '.join(missing)}. "
                        f"Continue — do not stop until both are done."
                    )
                    _step("EARLY_STOP_GUARD", missing=str(missing))
                    contents.append(types.Content(role="user", parts=[types.Part(text=reminder)]))
                    continue
                final = " ".join(p.text for p in candidate.content.parts if p.text).strip()
                _step("AGENT_DONE", message=final or "Agent completed.")
                break

            tool_results = []
            for part in tool_calls:
                fn   = part.function_call
                name = fn.name
                args = dict(fn.args) if fn.args else {}

                _step(f"TOOL_CALL:{name}",
                      args=str({k: str(v)[:80] for k, v in args.items()}))

                try:
                    if name == "discover_source_schema":
                        result = await _tool_discover_source_schema(state)

                    elif name == "discover_target_schema":
                        result = await _tool_discover_target_schema(state)

                    elif name == "sample_source_data":
                        tbl = args.get("table_name") or args.get("table") or args.get("tables", "")
                        if isinstance(tbl, list):
                            tbl = tbl[0] if tbl else ""
                        result = await _tool_sample_source_data(
                            table_name=str(tbl),
                            limit=int(args.get("limit", 10)),
                            state=state,
                        )

                    elif name == "write_and_test_mapping":
                        result = await _tool_write_and_test_mapping(
                            script=args.get("script", ""),
                            state=state,
                        )

                    elif name == "store_promotion_config":
                        result = await _tool_store_promotion_config(
                            config_json=args.get("config_json", "{}"),
                            state=state,
                        )

                    else:
                        result = {"status": "error", "error": f"Unknown tool: {name}. "
                                  "Use only the 6 allowed tools."}

                except Exception as exc:
                    result = {"status": "error",
                              "error":  f"{type(exc).__name__}: {exc}",
                              "traceback": traceback.format_exc()[:400]}

                _step(f"TOOL_RESULT:{name}",
                      status=result.get("status", ""),
                      summary=str(result)[:200])

                if result.get("status") == "error":
                    tool_failures[name] = tool_failures.get(name, 0) + 1
                    if tool_failures[name] >= MAX_TOOL_FAILURES:
                        aborted_tool = name

                tool_results.append(
                    types.Part(function_response=types.FunctionResponse(
                        name=name, response=result
                    ))
                )

            contents.append(types.Content(role="user", parts=tool_results))

            # Per-tool failure cap — stop burning turns on a tool that keeps failing
            if aborted_tool:
                _step("TOOL_FAILURE_LIMIT",
                      message=f"{aborted_tool} failed {MAX_TOOL_FAILURES} times — aborting early.")
                break

        else:
            _step("MAX_TURNS_REACHED", message=f"Hit safety limit of {MAX_TURNS} turns.")

    finally:
        await state.source_pool.close()
        await state.target_pool.close()

    if state.cleaned_df is None:
        reason = (f"tool '{aborted_tool}' failed {MAX_TOOL_FAILURES} times"
                  if aborted_tool else f"after {turns_used} turns")
        raise RuntimeError(
            f"Agent did not complete mapping ({reason}). Check reasoning_trace.")

    return _build_result(
        trace_id, state.cleaned_df, state.source_tables, state.promotion_config,
        transform_script=state.transform_script, reasoning_trace=trace,
        turns_used=turns_used, mapping_source="agent",
    )


# ── Result builder + mapping reuse (cache / replay) ───────────────────────────

def _build_result(
    trace_id: str, cleaned_df: pd.DataFrame, source_tables: dict[str, pd.DataFrame],
    promotion_config: dict, *, transform_script: str | None,
    reasoning_trace: list, turns_used: int, mapping_source: str,
) -> dict:
    """Shared result shape for both the agent run and the deterministic replay."""
    cleaned_records = cleaned_df.to_dict(orient="records")

    unique_users: list[dict] = []
    if "scientist_name" in cleaned_df.columns:
        role_col = "scientist_role" if "scientist_role" in cleaned_df.columns else None
        if role_col:
            unique_users = (
                cleaned_df.groupby("scientist_name")[role_col]
                .first().reset_index()
                .rename(columns={"scientist_name": "name", role_col: "role"})
                .to_dict(orient="records")
            )

    source_count = sum(len(df) for df in source_tables.values())

    return {
        "trace_id":         trace_id,
        "source_row_count": source_count,
        "staged_row_count": len(cleaned_records),
        "cleaned_records":  cleaned_records,
        "promotion_config": promotion_config,
        "transform_script": transform_script,
        "mapping_source":   mapping_source,          # "agent" (LLM ran) | "cache" (replayed)
        "unique_users":     unique_users,
        "quality_verdict":  "WARN" if any(
            r.get("risk_level") == "review" for r in cleaned_records
        ) else "PASS",
        "schema_mapping":   {
            k: v for k, v in promotion_config.items()
            if k not in ("anomaly_thresholds",)
        },
        "reasoning_trace":  reasoning_trace,
        "turns_used":       turns_used,
    }


async def _schema_fingerprint(pool: asyncpg.Pool) -> str:
    """Stable hash of a database's shape (tables, columns, types). Order-independent."""
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT table_name, column_name, data_type
            FROM   information_schema.columns
            WHERE  table_schema = 'public'
            ORDER  BY table_name, ordinal_position
        """)
    payload = [[r["table_name"], r["column_name"], r["data_type"]] for r in rows]
    return hashlib.sha256(json.dumps(payload).encode()).hexdigest()[:16]


async def compute_schema_fingerprints(source_url: str, target_url: str) -> dict:
    """
    Fingerprint the (source, target) schema pair. A cached mapping is reusable only
    while this combined fingerprint is unchanged — a new column or type change busts it.
    """
    sp = await _create_pool(source_url)
    try:
        tp = await _create_pool(target_url)
    except Exception:
        await sp.close()
        raise
    try:
        src = await _schema_fingerprint(sp)
        tgt = await _schema_fingerprint(tp)
    finally:
        await sp.close()
        await tp.close()
    combined = hashlib.sha256(f"{src}:{tgt}".encode()).hexdigest()[:16]
    return {"combined": combined, "source": src, "target": tgt}


async def replay_mapping(
    trace_id: str, source_url: str, transform_script: str, promotion_config: dict,
) -> dict:
    """
    Deterministic replay of a cached mapping — NO LLM, NO agent loop. Loads source data,
    re-executes the cached transform (which recomputes anomaly thresholds on the new data),
    and returns the same result shape the agent would have. Read-only, like the agent.
    """
    sp = await _create_pool(source_url)
    try:
        source_tables = await _load_source_tables(sp)
    finally:
        await sp.close()

    cleaned_df = _execute_pandas_transform(source_tables, transform_script)
    return _build_result(
        trace_id, cleaned_df, source_tables, promotion_config,
        transform_script=transform_script,
        reasoning_trace=[{"action": "CACHE_REPLAY",
                          "message": "Reused a cached mapping for this schema pair; "
                                     "agent/LLM skipped. Transform re-run on current data."}],
        turns_used=0, mapping_source="cache",
    )
