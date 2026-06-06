# Contributing / Developer Guide

## Status

The **core architecture is frozen** and considered complete. Only minor additions or modifications
should be made, and only when strictly required. UI refinements are expected. When in doubt about a
change that touches core logic (agents, promotion, orchestration), open an issue and discuss first.

## Local setup

```bash
# Backend
cd backend
cp .env.example .env          # fill in real values
pip install -r requirements.txt
python -m uvicorn app.main:app --reload --port 8001

# Frontends (each in its own terminal)
cd frontend && npm install && npm run dev          # :3000  HITL console
cd abase-frontend && npm install && npm run dev    # :3001  source viewer
cd gds-frontend && npm install && npm run dev      # :3002  target viewer

# Reset demo data to a clean state
cd backend && python scripts/reset_demo.py
```

## Code layout

| Path | What lives here |
|---|---|
| `backend/app/agents/` | The agents and the single-shot critic / report fallback |
| `backend/app/api/` | HTTP endpoints; **all database writes live here**, never in agents |
| `backend/app/connectors/` | Async PostgreSQL pool creation/management |
| `backend/app/core/` | `llm.py` (Gemini client + model failover), `mapping.py` (validation, coercion, config) |
| `backend/scripts/` | Operational scripts: `reset_demo.py` (demo reset + re-seed), `apply_gds_ddl.py` (one-off DDL migration for existing deployments) |
| `*-frontend/` | Three independent Next.js apps |

## Conventions & invariants (please preserve)

1. **Agents never write to the database.** Add new write logic to `app/api/*`, behind
   `validate_identifier()` for any agent-supplied identifier.
2. **The LLM never computes exact figures.** Use pandas/numpy or SQL for math; the LLM reasons and
   narrates.
3. **All Gemini calls go through `generate_with_backoff()`** so they inherit model failover + retry.
4. **`promotion_config` is the contract** between the Migration Agent and the promotion code. If you
   change its shape, update both producers and consumers (`migration_agent.py`, `migration.py`,
   `review_agent.py`, `verification_agent.py`).
5. **Never commit `.env`.** Add new env vars to `backend/.env.example` (names + placeholders only).
6. **Logs** go to `backend/logs/backend.log` (git-ignored).

## Adding a tool to an agent

1. Implement the tool function (read-only or in-memory).
2. Register it in that agent's `TOOL_DEFINITIONS` (Gemini `FunctionDeclaration`).
3. Add a dispatch branch in the agent's tool-calling loop.
4. For the Verification Agent, decide whether it belongs in `MANDATORY_TOOLS` (always run) or is
   discretionary (investigation only).

## Testing changes manually

There is no automated test suite yet. Validate changes by running an end-to-end cycle:
`reset_demo.py` → run migration (`POST /api/agent/run`) → review flagged rows → generate report
(`POST /api/report/{trace_id}`), watching `backend/logs/backend.log`.

## Commit messages

Use clear, imperative subjects (e.g. "Add upsert-key constraint guard to promotion"). Keep core
architecture changes in separate, well-described commits.
