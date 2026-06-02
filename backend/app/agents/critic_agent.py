"""
Mapping Critic Agent — Proposer-Critic Pattern
================================================
An independent agent that AUDITS the Migration Agent's proposed promotion_config
BEFORE any data is promoted. It does not re-do the mapping — it judges it.

This is the "critic" half of the proposer-critic pattern:
  - Migration Agent  = proposer (produces the mapping)
  - Mapping Critic    = critic   (reviews the mapping for correctness)

Single-shot Gemini call. Read-only. Returns a structured verdict (APPROVE / FLAG)
with per-field findings. On FLAG, the findings are what a human reviews at Gate 1.
"""

import json
import logging
import uuid as uuid_mod
from datetime import datetime, timezone

from google.genai import types

from app.connectors import get_gds_pool
from app.core.llm import get_client, generate_with_backoff, MODEL as _MODEL
from app.core.mapping import validate_identifier as _safe_table

logger = logging.getLogger(__name__)


# ── Data gathering ────────────────────────────────────────────────────────────

async def _gather_critic_data(trace_id: str) -> dict:
    pool = await get_gds_pool()
    tid  = uuid_mod.UUID(trace_id)

    async with pool.acquire() as conn:
        plan_row = await conn.fetchrow(
            "SELECT plan_json FROM migration_plans WHERE trace_id=$1", tid)
        if not plan_row:
            return {"error": "No migration plan found for this trace_id."}

        config = json.loads(plan_row["plan_json"]) \
                 if isinstance(plan_row["plan_json"], str) else plan_row["plan_json"]

        entity_tbl  = config.get("entity_table",  {}).get("name", "")
        records_tbl = config.get("records_table", {}).get("name", "")

        # Target schema for the two production tables (parameterized — safe)
        target_schema: dict = {}
        auto_filled: set = set()
        for t in (entity_tbl, records_tbl):
            if not t:
                continue
            cols = await conn.fetch("""
                SELECT column_name, data_type, is_nullable, column_default
                FROM   information_schema.columns
                WHERE  table_schema = 'public' AND table_name = $1
                ORDER  BY ordinal_position
            """, t)
            target_schema[t] = [
                {"column": c["column_name"], "type": c["data_type"],
                 "nullable": c["is_nullable"],
                 "has_default": c["column_default"] is not None}
                for c in cols
            ]
            # Any column with a DB default is auto-filled — never needs a mapping
            for c in cols:
                if c["column_default"] is not None:
                    auto_filled.add(c["column_name"])

        # Columns the promotion infrastructure fills itself (not from source data).
        # NOTE: source_experiment_id is NOT here — the agent maps it from source data.
        entity_pk = config.get("entity_table",  {}).get("pk", "")
        fk_col    = config.get("records_table", {}).get("fk_column", "")
        auto_filled.update({c for c in (entity_pk, fk_col, "trace_id", "approved_by") if c})

        # Sample of staged rows — shows the agent's intermediate column names + real values
        staging_tbl = _safe_table(config.get("staging_table", "gds_staging_experiments"))
        sample_rows = await conn.fetch(
            f"SELECT data FROM {staging_tbl} WHERE trace_id=$1 LIMIT 5", tid)
        samples = []
        for r in sample_rows:
            d = r["data"] if isinstance(r["data"], dict) else json.loads(r["data"])
            samples.append(d)

    return {
        "trace_id":       trace_id,
        "promotion_config": config,
        "target_schema":  target_schema,
        "auto_filled_columns": sorted(auto_filled),
        "sample_staged_rows": samples,
    }


# ── System prompt ─────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a schema-mapping critic for a data migration system.

Another agent (the "proposer") produced a promotion_config — a mapping describing how
intermediate/staging columns are written into target production tables. Your job is to
AUDIT that mapping for correctness BEFORE any data is committed. You do NOT re-do the
mapping. You judge it.

You receive:
- promotion_config: the proposed mapping (entity_table + records_table column_maps, upsert keys)
- target_schema: the actual columns + types of the target tables
- auto_filled_columns: columns managed by the infrastructure, NOT mapped from source
- sample_staged_rows: real values for the intermediate columns being mapped

INFRASTRUCTURE-MANAGED COLUMNS — READ THIS FIRST:
Columns listed in `auto_filled_columns` are populated and linked entirely by the deterministic
promotion infrastructure — they are NEVER written from incoming source values. In particular:
  - A primary/foreign key that is a generated UUID (a UUID column with a DB default): the infra
    INSERTs the entity, lets the database generate the UUID, then uses that generated UUID as the
    foreign key on the child record. Source values are never cast into it.
  - trace_id, approved_by, and any column with a DB default.
Therefore, for ANY column in `auto_filled_columns`, do NOT raise a type-mismatch, semantic-mismatch,
missing-mapping, or upsert-key finding. A source field that merely shares its name (e.g. an integer
`gds_user_id` present in the staged data) is NOT evidence of a bad mapping — the infra does not write
it into that column. Treating an auto-generated UUID PK/FK as an "integer → UUID type mismatch" is a
FALSE POSITIVE and must not be reported.

UPSERT KEYS are validated and auto-corrected by the infrastructure against the target table's real
UNIQUE / PRIMARY KEY constraints before use: a proposed key that does not match a real constraint is
transparently replaced with one that does. So an imperfect upsert-key proposal is at most an "info"
note — never an "error" or "warning".

CHECK FOR (reason from the data, do not just pattern-match names):
1. TYPE MISMATCH — a value mapped into a target column of an incompatible type
   (e.g. a text ID written into a numeric/double column, a string into a timestamp).
   This applies ONLY to columns actually mapped from source data — NEVER to
   `auto_filled_columns` (see the infrastructure-managed rule above).
2. SEMANTIC MISMATCH — a mapping where the source meaning clearly does not match the
   target column (e.g. an identifier mapped into a measurement/signal column).
3. MISSING MAPPING — a target column that has an obvious source equivalent in the
   sample data but was left unmapped.
   CRITICAL: Columns listed in auto_filled_columns are populated automatically by the
   infrastructure (DB defaults, primary/foreign keys, trace_id, approved_by). NEVER
   flag an auto_filled column as a missing mapping — that is expected and correct.
4. UNIT / SCALE RISK — names or values suggesting a unit difference
   (e.g. concentration_um vs concentration — confirm no conversion is silently lost).
5. UPSERT KEY SANITY — only an "info" note at most (the infra auto-corrects upsert keys
   against real constraints; never flag this as error/warning, and never for auto_filled columns).

For each issue, assign severity:
- "error"   = will corrupt data or break promotion (e.g. type mismatch)
- "warning" = likely wrong or risky, human should confirm
- "info"    = minor note, not blocking

VERDICT:
- "APPROVE" if there are no error-level findings (warnings/info are acceptable to note)
- "FLAG"    if there is ANY error-level finding, OR multiple serious warnings

Respond with ONLY valid JSON in exactly this shape:
{
  "verdict": "APPROVE" | "FLAG",
  "confidence": "high" | "medium" | "low",
  "summary": "one-sentence overall judgment",
  "findings": [
    {
      "severity": "error" | "warning" | "info",
      "field": "<source_col -> target_col>",
      "issue": "what is wrong or risky",
      "recommendation": "what to do about it"
    }
  ]
}
If the mapping is clean, return verdict APPROVE with an empty or info-only findings list.
"""


# ── Critic run ────────────────────────────────────────────────────────────────

async def run_critic_agent(trace_id: str) -> dict:
    data = await _gather_critic_data(trace_id)
    if data.get("error"):
        return {"trace_id": trace_id, "error": data["error"], "verdict": None}

    client = get_client()

    prompt = f"""Audit this proposed mapping.

PROMOTION CONFIG (the proposed mapping):
{json.dumps(data["promotion_config"], indent=2)}

TARGET SCHEMA (actual production table columns + types):
{json.dumps(data["target_schema"], indent=2)}

AUTO-FILLED COLUMNS (populated by infrastructure — do NOT flag these as missing mappings):
{json.dumps(data["auto_filled_columns"], indent=2)}

SAMPLE STAGED ROWS (real values for the intermediate columns being mapped):
{json.dumps(data["sample_staged_rows"], indent=2, default=str)}

Return your verdict as JSON.
"""

    response = await generate_with_backoff(
        client,
        contents=[types.Content(role="user", parts=[types.Part(text=prompt)])],
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            temperature=0.1,
            response_mime_type="application/json",
        ),
        model=_MODEL,
    )

    raw = ""
    if response.candidates and response.candidates[0].content:
        raw = " ".join(p.text for p in response.candidates[0].content.parts if p.text).strip()

    try:
        verdict = json.loads(raw)
    except json.JSONDecodeError:
        verdict = {
            "verdict": "FLAG",
            "confidence": "low",
            "summary": "Critic returned unparseable output — manual review required.",
            "findings": [{"severity": "warning", "field": "-",
                          "issue": "Could not parse critic response", "recommendation": "Review manually"}],
            "raw": raw[:500],
        }

    logger.info("Critic verdict for trace=%s: %s (%d findings)",
                trace_id, verdict.get("verdict"), len(verdict.get("findings", [])))

    return {
        "trace_id":      trace_id,
        "generated_at":  datetime.now(timezone.utc).isoformat(),
        "verdict":       verdict.get("verdict"),
        "confidence":    verdict.get("confidence"),
        "summary":       verdict.get("summary"),
        "findings":      verdict.get("findings", []),
        "reviewed_mapping": data["promotion_config"],
    }
