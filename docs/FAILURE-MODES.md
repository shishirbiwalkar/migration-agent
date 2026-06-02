# Failure Modes: LLM Unavailability & Hallucination

How the pipeline behaves when the LLM (Gemini) is overloaded or wrong. These are two
different problems with two different answers.

- **LLM unavailable** (overloaded, retries exhausted) → **fail clean, never corrupt.**
- **LLM wrong** (hallucinates a mapping) → **never trust the output; every LLM claim is
  re-derived or validated by deterministic code before any data moves.**

The unifying principle: the LLM is read-only and never produces a final number. It *proposes*;
deterministic infrastructure *verifies and executes*.

---

## 1. LLM overloaded / out of retries

### Failover first, abort second

`app/core/llm.py` (`generate_with_backoff`) does not sleep on a single overloaded model. On a
transient error (`429 / 503 / UNAVAILABLE / RESOURCE_EXHAUSTED / overloaded`) it fails over across
sibling Gemini models, which have independent load:

```
gemini-2.5-flash → gemini-2.0-flash → gemini-2.5-flash-lite
```

Only if the **whole chain** fails transiently in one round does it back off (2s → 4s → 8s) and
retry the chain, for `max_attempts` rounds (default 4). A **non-transient** error on the requested
model (e.g. a real 400) is raised immediately — genuine bugs are never masked behind a fallback.

When the chain is genuinely exhausted, `generate_with_backoff` **raises**. What happens next depends
on the caller — and no caller corrupts data:

| Component | Behaviour when LLM is fully unavailable | Net effect |
|---|---|---|
| **Migration Agent** (`agents/migration_agent.py`) | The raise propagates out of the agent loop; `api/agent.py` catches it → **HTTP 500, zero DB writes** | Source untouched; staging untouched. Re-run later. |
| **Mapping Critic** (`api/agent.py` Step 3b) | Wrapped in `try/except` → `critic_flagged = False` → pipeline proceeds | ⚠️ **fail-open** — see §3 |
| **Verification Agent** (`agents/verification_agent.py`) | Falls back to the deterministic `report_agent` | Report still produced from real DB numbers |

**Key property:** the Migration Agent is read-only and aborts *before* any promotion. "Out of
retries" therefore means **the run does not happen** — not "the run happens incorrectly." Combined
with the soft-delete change (source is marked `migrating`, never deleted before GDS commits), a
mid-run LLM outage leaves the source fully recoverable.

### Agent-internal limits

Even when the LLM *is* responding, the Migration Agent bounds its own work:

- `MAX_TURNS = 15` — hard ceiling on agent turns (`migration_agent.py:51`).
- `MAX_TOOL_FAILURES = 5` — if any single tool errors 5 times, the agent aborts with a
  `RuntimeError` rather than looping forever (`migration_agent.py:540, 664`).

---

## 2. Hallucination

The LLM can return confident, wrong output. The defense is not "prevent hallucination" — it is to
make every LLM claim **falsifiable** and route the un-verifiable ones to a human. Each layer below
runs *before* data reaches production.

| # | LLM claim | Deterministic check that catches it | On failure |
|---|---|---|---|
| 1 | "This pandas transform is correct" | Script is **executed against real source data in-memory** (`_tool_write_and_test_mapping`). Output must be a non-empty DataFrame. | Self-repair loop; 5 failures → abort |
| 2 | "Map to table/column X" | `validate_identifier` checks the name is a real, safe identifier | `ValueError` → no write |
| 3 | "Use these ON CONFLICT keys" | `_resolve_conflict_target` matches the proposal against **real** UNIQUE/PK constraints, falls back to a real one | Uses real constraint, or DB raises a clear error |
| 4 | "These source fields exist" | `_promote_rows` requires every `column_map` key to be present in staging data | `RuntimeError` with the actual vs expected keys |
| 5 | "This row is anomalous / clean" | Risk is computed by **pandas/numpy** (mean ± 2σ), not the LLM's opinion | n/a — LLM doesn't decide this |
| 6 | "The whole mapping is sound" | **Mapping Critic** audits `promotion_config` → `APPROVE` / `FLAG`. `FLAG` forces every row to mandatory HITL | Auto-promote bypassed |
| 7 | "The migration succeeded" | **Verification Agent** independently recomputes the threshold and reconciles staged-vs-production values | Report flags `NEEDS REVIEW` |

The agent **never writes to a database** and **never reports a number it computed itself** — the
verification report's figures all come from deterministic tools.

---

## 3. Residual risks (stated plainly)

Two gaps the deterministic guards above **cannot** close on their own:

### 3a. Structurally valid but semantically wrong mapping

If the LLM maps a source column to the *wrong but type-compatible* target column (e.g. `raw_value`
to the wrong float field — a real column, correct type, present in the data), every deterministic
check in §2 passes. Only the **Mapping Critic** (layer 6) and **Gate 1 human review** can catch a
semantic error. This is the primary reason the critic `FLAG` verdict is *enforced* (forces HITL),
not merely surfaced.

### 3b. Critic is fail-open

Today, if the Critic's LLM call fails (e.g. Gemini down), the pipeline proceeds **without** the
critic's verdict (`critic_flagged = False`), and auto-promote runs unguarded. For most runs this is
acceptable; for a high-assurance posture it is the weakest link, because the one layer designed to
catch §3a is skipped exactly when the LLM infrastructure is unhealthy.

**Hardening option (one-line policy change):** treat *critic-unavailable* the same as `FLAG` —
force all rows to HITL when no verdict could be obtained. This makes the critic **fail-closed**:

```python
# api/agent.py, Step 3b — fail-closed variant
except Exception as e:
    log.warning("Mapping critic unavailable — failing closed to HITL: %s", e)
    critic_flagged = True   # no verdict → mandatory human review
```

Trade-off: every run during a Gemini outage requires a human, even runs that would have been clean.
This is a deliberate availability-vs-assurance choice — left as fail-open by default and documented
here so the decision is explicit rather than accidental.

---

## Summary

- **Overload / out of retries** → the run aborts cleanly with no writes; source data is intact and
  recoverable. Failure is *refusal to act*, not corrupted action.
- **Hallucination** → caught by a stack of deterministic checks (execution, identifier validation,
  constraint resolution, statistical classification), with the Critic + HITL as the backstop for
  semantic errors the deterministic layer cannot see.
- **Known weak link** → the critic is fail-open; flip it to fail-closed (above) if assurance
  matters more than availability during an LLM outage.
