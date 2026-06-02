# Deployment & Fallback

How to deploy the Migration Agent, and how the system behaves when a component fails in production.

**The hard parts are already managed.** Both Postgres databases (Supabase) and the LLM (Google
Gemini) are hosted SaaS — nothing to deploy or scale there. What ships is one **stateless** FastAPI
backend plus three Next.js frontends. That keeps deployment small.

---

## 1. Topology

```
  Vercel                          Render/Railway/Fly/Cloud Run         Managed SaaS
  ──────                          ────────────────────────────         ────────────
  frontend      (HITL, :3000) ┐
  abase-frontend(view, :3001) ┼─▶  FastAPI backend (stateless)  ─┬─▶  Supabase ABASE (Postgres)
  gds-frontend  (view, :3002) ┘    uvicorn, /health probe        ├─▶  Supabase GDS   (Postgres)
                                                                 └─▶  Google Gemini  (LLM)
```

The backend holds **no state** — every durable fact lives in GDS Postgres (staging, plans, audit
log). That is what makes it safe to restart, replace, or run as multiple replicas.

---

## 2. Tier A — Pilot / demo deploy (do this now)

| Piece | Where | Notes |
|---|---|---|
| FastAPI backend | One container on Render / Railway / Fly / Cloud Run | `uvicorn app.main:app --host 0.0.0.0 --port $PORT`. Platform health probe → `GET /health`. |
| 3 Next.js frontends | Vercel (one project each) | `next build` + `next start`. Set the backend base URL per app. |
| Secrets | Platform secret store (**never** `.env` in the repo) | `GEMINI_API_KEY`, `ABASE_DATABASE_URL`, `GDS_DATABASE_URL`, `FRONTEND_URL` |
| DB connection | Supabase **pooler (pgbouncer)** endpoint | See connection-budget note in §4.3 |
| Schema | Run `schema_abase.sql` / `schema_gds.sql` in each Supabase project | Includes the `migration_status` `ALTER` from the soft-delete fix |

Sufficient for a CEO walkthrough or a small pilot.

### CORS caveat
`main.py` allows a single origin from `FRONTEND_URL` (default `http://localhost:3000`). With three
deployed frontends you must allow all three origins, not just one — update the CORS `allow_origins`
list before the ABASE/GDS viewers can call the API.

### Dependency caveat
`requirements.txt` does not list `numpy` explicitly, though the code imports it (it arrives
transitively via pandas). Pin it explicitly before building a production image.

---

## 3. Tier B — Production hardening (when it's real)

1. **Make the migration run asynchronous** (most important — see §4.2).
2. Containerize the backend with a pinned `requirements.txt` (add `numpy`).
3. Run **2+ replicas** behind a load balancer; `/health` gates routing.
4. Real secret manager + rotation for `GEMINI_API_KEY`.
5. Decide the **critic fail-open vs fail-closed** policy (see FAILURE-MODES.md §3b).

---

## 4. Fallback: what happens when something fails mid-flight

The design already makes most failures safe. The principle: **a failed run is safe to retry**,
because the agent is read-only, source is never deleted before GDS commits, and promotion is
idempotent.

### 4.1 Backend process dies mid-migration
- Agent is read-only; source is only marked `migration_status='migrating'` (not deleted).
- Any uncommitted GDS transaction rolls back; UNLOGGED staging is transient by design.
- **Fallback: re-run the trace.** Re-runs are idempotent — `ON CONFLICT` upserts plus
  `UNIQUE(gds_user_id, well_position)` prevent duplicate production rows.

### 4.2 HTTP request times out  ← addressed by the async path
- `POST /api/agent/run` runs the migration synchronously (up to `MAX_TURNS=15` LLM turns), which can
  exceed a 30–60s gateway timeout. It remains available for short/demo runs and backward compat.
- **Now available:** `POST /api/agent/run/async` returns `{ trace_id, status: queued }` immediately
  (HTTP 202) and runs the pipeline in the background; poll `GET /api/agent/jobs/{trace_id}` for
  status + result. Use this path behind a load balancer.
- **Still future:** the background task is **in-process** (lost on restart, single replica). A
  production deploy wants a durable queue with dedicated workers and checkpoint/resume so a run
  survives a pod restart and multiple replicas coordinate. The `migration_jobs` table is already the
  state backbone for that.

### 4.3 A database is unreachable
- `asyncpg` pool `acquire()` fails → the request errors cleanly, **no partial write**.
- **Fallback:** `/health` should verify *both* pools so the LB stops routing to a broken instance;
  add short retry/backoff on transient connection errors.
- **Connection budget:** pools are `min_size=2, max_size=10` **per process**. With `R` replicas
  against two databases that is up to `R × 10 × 2` server connections — stay under Supabase's limit
  by using the pooler endpoint and/or lowering `max_size`.

### 4.4 LLM unavailable
- Already handled (see FAILURE-MODES.md): model failover chain → clean abort for the Migration
  Agent (HTTP 500, no writes), deterministic `report_agent` fallback for the Verification Agent.
- **Open decision:** the Mapping Critic is fail-open — flip to fail-closed if assurance matters more
  than availability during an LLM outage (FAILURE-MODES.md §3b).

---

## 5. Summary

- Deploy = one stateless backend (Render/Railway/Fly/Cloud Run) + three frontends (Vercel); the
  databases and LLM are already managed.
- The system fails **safe, not corrupt**: a dead run leaves source intact and is safe to re-run
  (idempotent promotion).
- The one change to make before real traffic: run the migration **asynchronously** so it cannot be
  killed by an HTTP timeout.
