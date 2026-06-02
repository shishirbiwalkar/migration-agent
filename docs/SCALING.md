# Scaling

How the Migration Agent scales, and the order in which limits actually bite. See DEPLOYMENT.md for
hosting/fallback and FAILURE-MODES.md for LLM resilience.

**Right-sizing up front:** at the current scale (≈25 researchers, hundreds of wells) none of this is
a problem — the system runs comfortably as-is. This document is the order to address things *as
volume grows*, not a backlog for today.

The backend is stateless (all state in GDS Postgres), so "scale the backend" is trivial. The real
ceilings are elsewhere, in this order:

---

## Ceiling 1 — LLM quota (hits first; also the biggest lever) — ✅ IMPLEMENTED

**What breaks:** each run makes up to `MAX_TURNS = 15` Gemini calls. Free tier is 5 req/min, so a
single run can exhaust it. The LLM is the throughput bottleneck.

**Why it's the biggest lever:** the LLM does exactly *one* job — derive the mapping. The
architecture separates **derive (LLM)** from **execute (deterministic infra)**.

**What's now built:** a mapping cache. Each `(source, target)` schema pair is fingerprinted
(`compute_schema_fingerprints`, a hash of tables/columns/types). On a run:
- **Cache hit** (schema unchanged) → `replay_mapping` loads source data and re-executes the stored
  transform, **skipping the LLM/agent entirely**. The transform recomputes anomaly thresholds on the
  new data, so a larger batch is classified on its own distribution.
- **Cache miss** (first time, or schema drifted) → the agent runs and the learned transform +
  `promotion_config` are stored in `migration_mappings` for next time.

So migrating a batch of 20 then a batch of 40 with the **same schema** uses the agent once and
replays deterministically thereafter — zero LLM calls on the second batch. A new column or type
change busts the fingerprint and safely forces a re-derive.

Secondary levers: paid Gemini tier (higher RPM), fewer turns, cheaper models for sub-steps.

---

## Ceiling 2 — Synchronous run model (limits concurrency) — ✅ PARTIALLY IMPLEMENTED

**What breaks:** a fully synchronous `POST /run` executes all 15 turns inside the HTTP request, so
long runs exceed gateway timeouts and few can proceed at once.

**What's now built:** an async path alongside the (still backward-compatible) synchronous one.
- `POST /api/agent/run/async` → creates a `migration_jobs` row, launches the pipeline as a
  background task, and returns `{ trace_id, status: queued }` (HTTP 202) immediately.
- `GET /api/agent/jobs/{trace_id}` → poll for `queued | running | succeeded | failed` + the result.
- `POST /api/agent/run` is unchanged (runs synchronously, returns the full result) so the existing
  ABASE console keeps working.

**Still future (honest caveat):** the background task is **in-process** — it does not survive a
backend restart and does not coordinate across replicas. Production-grade scaling still wants a
durable queue (e.g. a `queued` row picked up by dedicated workers) with checkpoint/resume, and
worker count **bounded by the LLM rate limit**, not CPU.

---

## Ceiling 3 — In-memory pandas full-table load (data-volume ceiling)

**What breaks:** the agent loads **entire** source tables with `SELECT * FROM <table>` into pandas
(`migration_agent.py`), transforming in memory. Fine at hundreds of rows; at millions, one process
holds the whole table in RAM and falls over. This is the hard architectural ceiling.

**Move:** shard the migration by entity. The `trace_id` + per-researcher model already provides a
natural sharding key — migrate per-researcher (or per-batch) so each run's memory is bounded and
runs parallelize across workers. For very large tables, push deterministic transforms into SQL
rather than pandas.

---

## Ceiling 4 — Database connection amplification

**What breaks:** each concurrent run opens its **own** source + target pools via `_create_pool`
(`min_size=1, max_size=5` each → up to 10 connections per run) *on top of* the shared singleton
pools (`min_size=2, max_size=10` each → ~20). ~10 concurrent runs ≈ 100+ Supabase connections — the
connection limit is reached before CPU is.

**Move:**
- Route through Supabase's **pgbouncer pooler** endpoint.
- Reuse shared pools instead of per-run `_create_pool` where the source/target are the default DBs.
- Lower `max_size` and cap concurrent runs.

---

## Ceiling 5 — HITL human throughput

**What breaks:** if many rows flag for `review`, the bottleneck becomes *humans*, which no
infrastructure scaling fixes.

**Move:**
- Keep the auto/review classifier (mean ± 2σ) well-tuned to minimize false-positive flags.
- Use the **Review Resolution Agent** to batch-resolve via plain-English instructions, and bulk
  approve/exclude in the HITL Console.

---

## Summary

| Order | Ceiling | Highest-leverage move | Status |
|---|---|---|---|
| 1 | LLM quota | Derive mapping once per schema pair, **cache + replay** | ✅ implemented |
| 2 | Synchronous run | Async job submit + status polling | ✅ partial (in-process) |
| 3 | In-memory pandas | Shard by `trace_id`/researcher; SQL transforms for huge tables | future |
| 4 | DB connections | pgbouncer pooler; reuse pools; lower `max_size` | future |
| 5 | Human review | Tune classifier; batch via Review Resolution Agent | n/a |

The defining insight: the LLM is needed to *learn* a mapping, not to *apply* it. Cache the learned
mapping and the system scales like ordinary Postgres infrastructure.
