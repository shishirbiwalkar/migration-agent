"""
Orchestrator Agent
==================
A true AI orchestrator that reasons about the migration pipeline and delegates
to specialist sub-agents. Unlike the deterministic pipeline in api/agent.py,
this LLM-driven agent decides what to do next based on what it finds.

Key decisions the orchestrator makes:
  - Verifies backup succeeded before proceeding (stops if not)
  - Assesses anomaly count — escalates early if too many
  - If critic finds errors → escalates ALL rows to HITL
  - If critic finds warnings only → proceeds with auto-promotion, notes warnings
  - Can loop back if a sub-agent fails (e.g. retry mapping once)

Enable:  ORCHESTRATOR_AGENT=true  in .env
Revert:  ORCHESTRATOR_AGENT=false (falls back to deterministic pipeline in api/agent.py)

Tools (what the orchestrator can call):
  1. trigger_backup          — cloud snapshot of source DB before anything
  2. run_sme_etl_agent       — schema discovery + semantic mapping + transform
  3. write_to_staging        — persist cleaned records to staging buffer
  4. run_qa_critic           — independent review of the column mapping
  5. auto_approve_clean_rows — promote safe rows straight to production
  6. escalate_all_to_review  — send every row to HITL (critic error path)
  7. get_pipeline_status     — read current staging counts
"""

import uuid
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from google.genai import types

from app.core.llm import get_client, generate_with_backoff, MODEL as _MODEL
from app.connectors import get_gds_pool

logger    = logging.getLogger(__name__)
MAX_TURNS = 25


# ── Per-run state ─────────────────────────────────────────────────────────────

@dataclass
class _State:
    trace_id:         str
    source_url:       str
    target_url:       str
    initiated_by:     str
    backup_result:    dict | None = None
    agent_result:     dict | None = None      # full result from run_sme_etl_agent
    cleaned_records:  list        = field(default_factory=list)
    promotion_config: dict        = field(default_factory=dict)
    staging_count:    int         = 0
    critic_result:    dict | None = None
    auto_promoted:    int         = 0
    pending_review:   int         = 0


# ── Tool implementations ──────────────────────────────────────────────────────

async def _tool_trigger_backup(state: _State) -> dict:
    from app.core.backup import trigger_backup
    from app.core.backup_store import save_backup
    import urllib.parse

    backup = await trigger_backup(state.source_url, state.trace_id)
    state.backup_result = backup

    parsed      = urllib.parse.urlparse(state.source_url)
    source_host = parsed.hostname or state.source_url.split("@")[-1].split("/")[0]

    await save_backup(
        trace_id=state.trace_id, source_host=source_host,
        provider=backup["provider"], snapshot_id=backup.get("snapshot_id"),
        status=backup["status"], metadata=backup.get("metadata", {}),
    )

    return {
        "status":        "success",
        "provider":      backup["provider"],
        "snapshot_id":   backup.get("snapshot_id"),
        "backup_status": backup["status"],
        "note":          "Call check_backup_status next to wait for completion before proceeding.",
    }


async def _tool_check_backup_status(state: _State) -> dict:
    from app.core.backup import check_backup_status

    if not state.backup_result:
        return {"status": "error", "error": "No backup triggered yet. Call trigger_backup first."}

    provider    = state.backup_result.get("provider", "none")
    snapshot_id = state.backup_result.get("snapshot_id")
    bstatus     = state.backup_result.get("backup_status", state.backup_result.get("status", ""))

    # Provider=none or skipped means dev environment — safe to proceed
    if provider == "none" or bstatus == "skipped" or not snapshot_id:
        return {"status": "skipped", "confirmed": True,
                "message": "No backup provider configured (dev mode). Safe to proceed."}

    result = await check_backup_status(snapshot_id, provider)

    # Update stored status in local SQLite log
    from app.core.backup_store import update_backup_status
    await update_backup_status(state.trace_id, result["status"])

    confirmed = result["status"] == "available"
    return {
        "status":    result["status"],
        "confirmed": confirmed,
        "message":   result["message"],
        "proceed":   confirmed,
        "note":      "Proceed to run_sme_etl_agent." if confirmed
                     else "Do NOT proceed — backup not confirmed. Stop and report.",
    }


async def _tool_run_sme_etl_agent(state: _State) -> dict:
    from app.agents.migration_agent import run_migration_agent, compute_schema_fingerprints, replay_mapping
    from app.api.pipeline_helpers import store_migration_plan, mark_source_migrating

    # Cache replay if schema unchanged
    fp     = await compute_schema_fingerprints(state.source_url, state.target_url)
    pool   = await get_gds_pool()
    cached = None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT transform_script, promotion_config FROM migration_mappings WHERE schema_fingerprint=$1",
            fp["combined"])
        if row:
            await conn.execute(
                "UPDATE migration_mappings SET hit_count=hit_count+1, last_used_at=NOW() WHERE schema_fingerprint=$1",
                fp["combined"])
            cfg = row["promotion_config"]
            cached = {
                "transform_script": row["transform_script"],
                "promotion_config": cfg if isinstance(cfg, dict) else json.loads(cfg),
            }

    if cached:
        result = await replay_mapping(
            state.trace_id, state.source_url,
            cached["transform_script"], cached["promotion_config"])
    else:
        result = await run_migration_agent(
            trace_id=state.trace_id, source_url=state.source_url, target_url=state.target_url)
        if result.get("transform_script") and result.get("promotion_config"):
            async with pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO migration_mappings
                        (schema_fingerprint, source_fingerprint, target_fingerprint,
                         transform_script, promotion_config, derived_trace_id, last_used_at)
                    VALUES ($1,$2,$3,$4,$5,$6,NOW())
                    ON CONFLICT (schema_fingerprint) DO UPDATE SET
                        transform_script=EXCLUDED.transform_script,
                        promotion_config=EXCLUDED.promotion_config,
                        derived_trace_id=EXCLUDED.derived_trace_id,
                        last_used_at=NOW()
                """, fp["combined"], fp["source"], fp["target"],
                     result["transform_script"], json.dumps(result["promotion_config"]),
                     uuid.UUID(state.trace_id))

    state.agent_result    = result
    state.cleaned_records = result.pop("cleaned_records", [])
    state.promotion_config = result["promotion_config"]

    try:
        await mark_source_migrating(state.cleaned_records)
    except Exception as e:
        logger.warning("mark_source_migrating skipped: %s", e)

    await store_migration_plan(state.trace_id, state.promotion_config, state.initiated_by)

    review_count = sum(1 for r in state.cleaned_records if r.get("risk_level") == "review")
    auto_count   = sum(1 for r in state.cleaned_records if r.get("risk_level") == "auto")

    return {
        "mapping_source":  result.get("mapping_source"),
        "total_records":   len(state.cleaned_records),
        "auto_rows":       auto_count,
        "review_rows":     review_count,
        "quality_verdict": result.get("quality_verdict"),
        "turns_used":      result.get("turns_used", 0),
    }


async def _tool_write_to_staging(state: _State) -> dict:
    from app.api.pipeline_helpers import write_to_staging

    if not state.cleaned_records:
        return {"status": "error", "error": "No cleaned records — run run_sme_etl_agent first."}

    count = await write_to_staging(state.trace_id, state.cleaned_records, state.promotion_config)
    state.staging_count = count
    return {"staged": count, "staging_table": state.promotion_config.get("staging_table")}


async def _tool_run_qa_critic(state: _State) -> dict:
    from app.agents.critic_agent import run_critic_agent

    if not state.promotion_config:
        return {"status": "error", "error": "No promotion_config — run run_sme_etl_agent first."}

    result = await run_critic_agent(state.trace_id)
    state.critic_result = result

    findings  = result.get("findings", [])
    has_errors = any(f.get("severity") == "error" for f in findings)

    return {
        "verdict":     result.get("verdict"),
        "confidence":  result.get("confidence"),
        "summary":     result.get("summary"),
        "has_errors":  has_errors,
        "error_count": sum(1 for f in findings if f.get("severity") == "error"),
        "warn_count":  sum(1 for f in findings if f.get("severity") == "warning"),
        "findings":    findings,
    }


async def _tool_auto_approve_clean_rows(state: _State) -> dict:
    from app.api.migration import auto_approve_clean_rows
    from app.api.pipeline_helpers import purge_committed_sources

    result = await auto_approve_clean_rows(state.trace_id, approved_by="AI Orchestrator (auto)")
    state.auto_promoted  = result["auto_promoted"]
    state.pending_review = result["pending_review"]

    staging_table = state.promotion_config.get("staging_table", "gds_staging_experiments")
    try:
        n, deleted = await purge_committed_sources(state.trace_id, staging_table)
        logger.info("Purged %d fully-migrated sources: %s", n, deleted)
    except Exception as e:
        logger.warning("Source purge skipped: %s", e)

    return {
        "auto_promoted":  state.auto_promoted,
        "pending_review": state.pending_review,
        "hitl_required":  state.pending_review > 0,
        "hitl_url":       f"/review?trace_id={state.trace_id}" if state.pending_review > 0 else None,
    }


async def _tool_escalate_all_to_review(state: _State) -> dict:
    from app.api.pipeline_helpers import force_all_to_review

    staging_table = state.promotion_config.get("staging_table", "gds_staging_experiments")
    pool = await get_gds_pool()
    async with pool.acquire() as conn:
        forced = await force_all_to_review(conn, state.trace_id, staging_table)

    state.pending_review = forced
    return {
        "forced_to_review": forced,
        "hitl_url":         f"/review?trace_id={state.trace_id}",
        "reason":           "Critic found error-level findings — all rows require human approval",
    }


async def _tool_get_pipeline_status(state: _State) -> dict:
    staging_table = state.promotion_config.get("staging_table", "gds_staging_experiments") \
                    if state.promotion_config else "gds_staging_experiments"
    pool = await get_gds_pool()
    tid  = uuid.UUID(state.trace_id)
    async with pool.acquire() as conn:
        rows = await conn.fetch(f"""
            SELECT status, risk_level, COUNT(*) AS cnt
            FROM   {staging_table}
            WHERE  trace_id = $1
            GROUP  BY status, risk_level
        """, tid)
    summary = [{"status": r["status"], "risk_level": r["risk_level"], "count": r["cnt"]}
               for r in rows]
    return {"trace_id": state.trace_id, "staging_summary": summary}


# ── Tool definitions (Gemini format) ─────────────────────────────────────────

_TOOLS = types.Tool(function_declarations=[

    types.FunctionDeclaration(
        name="trigger_backup",
        description=(
            "ALWAYS call this first. Triggers a cloud infrastructure snapshot of the source DB. "
            "After calling this, you MUST call check_backup_status before doing anything else."
        ),
        parameters=types.Schema(type=types.Type.OBJECT, properties={}),
    ),

    types.FunctionDeclaration(
        name="check_backup_status",
        description=(
            "Polls the backup provider until the snapshot is confirmed complete. "
            "Call this after trigger_backup and wait until confirmed=true before proceeding. "
            "If confirmed=false, stop immediately — do not start migration."
        ),
        parameters=types.Schema(type=types.Type.OBJECT, properties={}),
    ),

    types.FunctionDeclaration(
        name="run_sme_etl_agent",
        description=(
            "Delegates schema discovery, semantic mapping, and data transformation to the "
            "SME+ETL specialist agent. Returns row counts and quality verdict. "
            "Call after backup is confirmed."
        ),
        parameters=types.Schema(type=types.Type.OBJECT, properties={}),
    ),

    types.FunctionDeclaration(
        name="write_to_staging",
        description=(
            "Persists the transformed records to the staging buffer. "
            "Call after run_sme_etl_agent succeeds."
        ),
        parameters=types.Schema(type=types.Type.OBJECT, properties={}),
    ),

    types.FunctionDeclaration(
        name="run_qa_critic",
        description=(
            "Delegates independent QA review of the column mapping to the Critic agent. "
            "Returns verdict (APPROVE/FLAG), has_errors flag, and findings. "
            "Call after write_to_staging."
        ),
        parameters=types.Schema(type=types.Type.OBJECT, properties={}),
    ),

    types.FunctionDeclaration(
        name="auto_approve_clean_rows",
        description=(
            "Promotes risk_level='auto' rows directly to production. "
            "Call only after critic APPROVES or critic has warnings-only (no errors). "
            "Never call if critic found error-level findings."
        ),
        parameters=types.Schema(type=types.Type.OBJECT, properties={}),
    ),

    types.FunctionDeclaration(
        name="escalate_all_to_review",
        description=(
            "Forces ALL staged rows to HITL review, bypassing auto-promotion. "
            "Call when critic finds error-level findings. "
            "Never call if critic APPROVED."
        ),
        parameters=types.Schema(type=types.Type.OBJECT, properties={}),
    ),

    types.FunctionDeclaration(
        name="get_pipeline_status",
        description="Returns current staging counts by status and risk_level. Use to verify state.",
        parameters=types.Schema(type=types.Type.OBJECT, properties={}),
    ),
])


# ── System prompt ─────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """You are the Orchestrator Agent for a data migration pipeline.
Your job is to reason about each step, interpret results, and decide what to do next.
You delegate work to specialist sub-agents via tools — you never implement logic yourself.

PIPELINE RULES:
1. Call trigger_backup FIRST — fires the snapshot request.
2. Call check_backup_status — poll until confirmed=true. If confirmed=false, STOP. Never migrate without a confirmed backup.
3. Call run_sme_etl_agent to get the schema mapping and transformed records.
4. Call write_to_staging to persist records.
5. Call run_qa_critic to independently validate the mapping.
6. Based on critic result:
   - verdict=APPROVE or (verdict=FLAG AND has_errors=false) → call auto_approve_clean_rows
   - verdict=FLAG AND has_errors=true → call escalate_all_to_review (never auto-approve)
7. Report the final outcome clearly: rows auto-promoted, rows pending human review, HITL URL if needed.

DECISION GUIDANCE:
- If run_sme_etl_agent returns review_rows > 50% of total → flag this as unusual before proceeding
- If backup status is 'skipped' (BACKUP_PROVIDER=none) → proceed but warn this is not production-safe
- If any tool returns an error → reason about whether to retry once or abort
- Your goal: migrate clean data automatically, send only genuine anomalies to humans

Respond concisely. After the final tool call, summarize: what was migrated, what needs human review, and why."""


# ── Orchestrator loop ─────────────────────────────────────────────────────────

async def run_orchestrator_agent(
    trace_id:     str,
    source_url:   str,
    target_url:   str,
    initiated_by: str,
) -> dict:
    client = get_client()
    state  = _State(trace_id=trace_id, source_url=source_url,
                    target_url=target_url, initiated_by=initiated_by)
    log    = logging.LoggerAdapter(logger, {"trace_id": trace_id})
    trace: list[dict] = []

    def _step(action: str, **kwargs):
        entry = {"action": action, "timestamp": datetime.now(timezone.utc).isoformat(),
                 **{k: str(v)[:300] for k, v in kwargs.items()}}
        trace.append(entry)
        log.info("ORCHESTRATOR [%s] %s", action, str(kwargs)[:120])

    contents: list[types.Content] = [
        types.Content(role="user", parts=[types.Part(text=(
            f"Orchestrate the migration pipeline for trace_id={trace_id}. "
            f"Follow the pipeline rules. Begin with trigger_backup."
        ))])
    ]

    _cfg = types.GenerateContentConfig(
        system_instruction=_SYSTEM_PROMPT,
        tools=[_TOOLS],
        temperature=0.1,
    )

    _step("ORCHESTRATOR_START")

    for turn in range(MAX_TURNS):
        response = await generate_with_backoff(client, contents=contents, config=_cfg, model=_MODEL)

        if not response.candidates:
            _step("NO_CANDIDATES")
            break

        candidate = response.candidates[0]
        if not candidate.content or not candidate.content.parts:
            _step("EMPTY_CONTENT")
            break

        contents.append(types.Content(role="model", parts=candidate.content.parts))
        tool_calls = [p for p in candidate.content.parts if p.function_call]

        if not tool_calls:
            final = " ".join(p.text for p in candidate.content.parts if p.text).strip()
            _step("ORCHESTRATOR_DONE", summary=final[:300])
            break

        tool_results = []
        for part in tool_calls:
            name = part.function_call.name
            _step(f"TOOL_CALL:{name}")

            try:
                if   name == "trigger_backup":          result = await _tool_trigger_backup(state)
                elif name == "check_backup_status":     result = await _tool_check_backup_status(state)
                elif name == "run_sme_etl_agent":       result = await _tool_run_sme_etl_agent(state)
                elif name == "write_to_staging":        result = await _tool_write_to_staging(state)
                elif name == "run_qa_critic":           result = await _tool_run_qa_critic(state)
                elif name == "auto_approve_clean_rows": result = await _tool_auto_approve_clean_rows(state)
                elif name == "escalate_all_to_review":  result = await _tool_escalate_all_to_review(state)
                elif name == "get_pipeline_status":     result = await _tool_get_pipeline_status(state)
                else:
                    result = {"status": "error", "error": f"Unknown tool: {name}"}
            except Exception as exc:
                result = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}

            _step(f"TOOL_RESULT:{name}", summary=str(result)[:200])
            tool_results.append(types.Part(function_response=types.FunctionResponse(
                name=name, response=result)))

        contents.append(types.Content(role="user", parts=tool_results))
    else:
        _step("MAX_TURNS_REACHED")

    if state.agent_result is None:
        raise RuntimeError("Orchestrator did not complete the migration pipeline.")

    return {
        **state.agent_result,
        "trace_id":        trace_id,
        "auto_approved":   state.auto_promoted,
        "pending_review":  state.pending_review,
        "hitl_required":   state.pending_review > 0,
        "hitl_url":        f"/review?trace_id={trace_id}" if state.pending_review > 0 else None,
        "status":          "pending_human_review" if state.pending_review > 0 else "auto_approved",
        "mapping_review":  state.critic_result,
        "critic_flagged":  (state.critic_result or {}).get("verdict") == "FLAG",
        "backup":          state.backup_result,
        "reasoning_trace": trace,
    }
