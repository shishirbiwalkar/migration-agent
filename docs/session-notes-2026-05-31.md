# Session Log — 2026-05-31 00:58 EDT

## What was done this session

### Architecture review
- Confirmed agent is genuinely agentic: semantic mapping, code generation, self-repair
- Confirmed infrastructure is deterministic: staging writes, promotion, HITL, audit
- Clarified what human task it saves: eliminates schema analysis + transformation scripting, leaves only judgment call (approve/reject anomalous rows)

### Bugs fixed
- numpy serialization crash in `_write_to_staging`
- `response.candidates[0]` IndexError on Gemini quota/safety response
- Pool resource leak — `try/finally` wraps agent loop
- Source pool leak — target pool failure now closes source pool
- Empty `promotion_config` silent skip → now raises HTTPException 500
- Dead `import asyncio` removed
- Empty `UPDATE SET` clause in `_promote_rows` → falls back to `DO NOTHING`
- String `trace_id` in `ai_cleaner.py` → now passes `uuid.UUID` to asyncpg
- `migration_plans` table added to `schema_gds.sql`
- `ai_cleaner.py` Gemini client moved inside function (no startup crash without API key)

### Genericity fixes
- `exclude_row`/`restore_row` now config-driven via `trace_id` query param
- `get_audit` staging summary now config-driven
- ABASE write-back removed from `approve()` — endpoint is now fully generic

### System prompt fixes
- Semantic mapping made explicit core responsibility of agent
- `discover_target_schema` tool note tells agent to ignore infrastructure tables
- Nullable column warning — agent must select only needed columns to avoid silent data loss
- `dropna` now logs warning when rows are dropped

### Files removed
- `backend/schema.sql` — old superseded schema with hardcoded columns
- `backend/test_pipeline.py` — stale test against old API
- `backend/__init__.py` — empty file
- `backend/.pytest_cache/` — pytest cache

## Current status
- Foundation is correct and architecture is clean
- All major bugs fixed
- Backend running on port 8001, both DB pools connected

## Only blocker for first successful run
`GEMINI_API_KEY` missing from `backend/.env`
1. Get key from https://aistudio.google.com/app/apikey
2. Add to `backend/.env`
3. Restart backend
4. Run: `curl -X POST http://localhost:8001/api/agent/run`

## Planned next features (post first successful run)
1. Mapping recommendation engine — expose `promotion_config` draft before execution
2. Source-to-target verification report — count source vs promoted rows
3. Metadata discovery enrichment — semantic labels stored in `migration_plans`

---

## Discussions after session save

### Is 648 lines standard for an agent?
- With a framework (LangChain, OpenAI Agents SDK): ~160-200 lines
- Without a framework (raw API like ours): ~400-450 lines standard
- Ours is 648 — extra ~200 lines is defensive production-grade code (error handling, pool lifecycle, logging, serialization)
- For interview: strength, not weakness — shows understanding of failure modes

### SDK / Framework decision
- Confirmed: staying on Gemini SDK (`google-genai`) — already wired up, no migration needed
- LangChain: NOT needed — adds abstraction and complexity with no benefit for single agent
- LangGraph: NOT needed yet — designed for multi-agent graphs with conditional routing
- No other SDK migration until after first successful run

### When to use LangGraph
- When agents coordinate with each other (Migration Agent → Validation Agent → Report Agent)
- When conditional routing is needed (validation fails → loop back to migration agent)
- That's the right time. Not now.

### Code length — future refactor plan
After first successful run, split `migration_agent.py` (648 lines) into:
```
app/agents/
    migration_agent.py     # loop + state only (~150 lines)
    tools/
        schema.py          # discover_source + discover_target
        sampler.py         # sample_source_data
        mapper.py          # write_and_test_mapping
        config.py          # store_promotion_config
    prompts.py             # SYSTEM_PROMPT
```
Same logic, better organisation. Do AFTER first run, not before.

### Is it a real AI Agent?
Yes. Three properties confirm it:
- **Perception** — reads real live databases it has never seen before
- **Reasoning** — LLM figures out `raw_value` = `signal` from schema + samples
- **Action** — writes code, runs it, reads traceback, self-repairs, produces config
The self-repair loop alone qualifies it. A script is told what to do. This agent figures it out.

---

## UI Design (agreed ~85%, pending final changes from user)

### ABASE Frontend
**Login Page** → Username + Password fields + Login button → Admin Console

**Admin Console**
- Table of all scientists: `ID | Name | Email | Last Login`
- Click any row → drill down to that scientist's experiments

**Scientist Drill Down**
- Header: name, email, department
- Experiment table: `Well | Value | Plate | Recorded At`
- Plain table, no analytics (legacy system)

---

### GDS Frontend
**Login Page** → same flow → GDS Admin Console

**Admin Console — novel additions vs ABASE**
- 4 summary cards at top: `Total Scientists | Migrated from ABASE | Native GDS | Flagged Wells`
- Scientist table: `ID | Name | Role | Avg Signal | Wells | Source (MIGRATED/NATIVE)`
- Search + Filter controls

**Scientist Drill Down — key differentiators**

1. **Migration provenance banner** — "Migrated from ABASE on [date] · Approved by [name] · Trace ID: [uuid]"
2. **Signal analytics card** — Avg Signal, Std Dev, Min, Max, Total Wells, Flagged count
3. **96-Well Plate Heatmap** — visual grid (A1→H12) color-coded by signal intensity + risk_level
   - 🟢 Clean (risk_level = auto)
   - 🟡 Review (risk_level = review)
   - 🔴 Outlier (extreme flagged)
4. **Well data table** — `Well | Signal | Quality Badge | Compound ID | Conc(µM) | Assay Type`
   - Compound ID, Concentration, Assay Type show `—` for migrated rows
   - Populate when scientist runs native GDS experiments

### Cost clarification (confirmed)
- Plate heatmap: zero extra API calls — pure frontend CSS grid using data already in DB
- Quality badges (🟢🟡🔴): zero cost — conditional rendering on `risk_level` column already stored
- Only Gemini agent run costs tokens — once per migration, not per page load

### Pending
- User has ~15% changes in mind — to be discussed next session
- Schema changes needed in GDS Supabase before building:
  ```sql
  ALTER TABLE gds_experiments
      ADD COLUMN IF NOT EXISTS plate_barcode   TEXT,
      ADD COLUMN IF NOT EXISTS compound_id     TEXT,
      ADD COLUMN IF NOT EXISTS concentration   DOUBLE PRECISION,
      ADD COLUMN IF NOT EXISTS assay_type      TEXT,
      ADD COLUMN IF NOT EXISTS run_condition   TEXT;
  ```
- Agent promotion_config fallback needs `plate_barcode` added to column_map

---

## Migration Control Panel — Positioning & Story

### Where to trigger migration (industry standard)
- Dedicated Migration Control Panel — separate from ABASE and GDS
- Same pattern as AWS DMS, Airbyte, Fivetran — migration tool sits between source and target
- Our HITL frontend (localhost:3000) IS this control panel

### Three windows for demo
| Window | URL | Role |
|--------|-----|------|
| Left | localhost:3001 | ABASE — source viewer (legacy) |
| Center | localhost:3000 | Migration Control Panel — trigger + review |
| Right | localhost:3002 | GDS — target viewer (modern) |

### Why it's separate — the story
- ABASE and GDS don't know about each other — they shouldn't
- Control panel is the only system that talks to both
- If ABASE is replaced tomorrow, GDS doesn't change — agent re-discovers schemas
- If GDS is upgraded, ABASE doesn't change
- Clean separation of concerns — same as every enterprise migration tool

### Data flow
```
ABASE ──read only──→ AI Agent ──transforms──→ Staging ──promotes──→ GDS
                          ↑
                    Control Panel
                    triggers this
```

### Demo script (3 sentences)
1. "On the left is ABASE — our legacy system. Scientists' data has lived here for years."
2. "In the center is the Migration Control Panel. I click Run Migration — the AI agent connects to both databases, figures out how the data maps across different column names, flags anomalous readings, and stages everything for review."
3. "On the right is GDS — our modern platform. After I approve the flagged rows, they appear in GDS with analytics, quality badges, and the full plate visualization — features ABASE never had."

---

## Live Progress Feed — Critical for Demo

### Problem
Right now clicking Run Migration shows nothing for 30-60 seconds — looks broken in a demo.

### Solution — Real-time agent progress via SSE (Server-Sent Events)
```
✅  Connecting to ABASE...            done
✅  Connecting to GDS...              done
✅  Discovering source schema...      done
✅  Discovering target schema...      done
✅  Sampling source data...           done
🔄  Writing transformation script...  in progress
⬜  Classifying anomalies...
⬜  Storing promotion config...
⬜  Writing to staging...
⬜  Auto-promoting clean rows...

Turn 4 of 15
```

### Implementation options
| Option | How | Complexity |
|--------|-----|------------|
| Polling | Frontend calls `/api/agent/status` every 2s | Simple |
| SSE (recommended) | Backend streams `_step()` logs to frontend in real time | Medium |

### Why SSE is recommended
- Agent already logs every step via `_step()` in migration_agent.py
- Just stream those logs to frontend instead of storing for later
- Watching agent think, discover, write code, self-repair LIVE is the most impressive demo moment
- Shows the agent is actually working — not just a button that waits

### Status — to be built after first successful run
