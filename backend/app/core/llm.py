"""
Shared Gemini access for all agents.
Single place for the client + a resilient call wrapper.

Resilience strategy (matters for live demos):
  On a transient error (429 / 503 / overload) we do NOT just sleep on the same
  model — that turns a Gemini-side overload into a multi-minute freeze. Instead we
  immediately fail over to a sibling Gemini model (same API, same key, just a
  different model id). Different model endpoints have independent load, so a 503 on
  one is usually served fine by another in <2s. Only if the WHOLE chain is transiently
  failing do we back off and retry the chain. All models are Gemini.
"""

import os
import asyncio
import logging

from google import genai

logger = logging.getLogger(__name__)

MODEL = "gemini-2.5-flash"

# Failover order. The requested model is tried first, then the rest of these.
# All share one API/key — only the model id changes.
# NOTE: gemini-2.0-flash was removed — it returns 404 NOT_FOUND on this API/key, so it
# only ever wasted a failover hop and pushed calls onto the weakest model.
FALLBACK_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
]

_TRANSIENT = ("429", "503", "UNAVAILABLE", "RESOURCE_EXHAUSTED", "overloaded")


def get_client() -> genai.Client:
    return genai.Client(api_key=os.environ["GEMINI_API_KEY"])


def _is_transient(err: Exception) -> bool:
    return any(t in str(err) for t in _TRANSIENT)


async def generate_with_backoff(client, *, contents, config,
                                model: str = MODEL, max_attempts: int = 4):
    """
    Call Gemini with model failover + backoff.

    Per round, we try the requested model, then each fallback, switching the instant
    one returns a transient error. If every model in the chain is transient, we back
    off (2s, 4s, 8s) and run another round. max_attempts = number of rounds.

    A NON-transient error on the requested model (e.g. a real 400) is raised
    immediately — we never mask a genuine bug behind a fallback. Non-transient errors
    on fallback models are skipped (they're best-effort alternates).
    """
    # Requested model first, then the remaining fallbacks (deduped, order preserved).
    chain = [model] + [m for m in FALLBACK_MODELS if m != model]
    last_exc: Exception | None = None

    for attempt in range(max_attempts):
        for idx, m in enumerate(chain):
            is_primary = idx == 0
            try:
                resp = await client.aio.models.generate_content(
                    model=m, contents=contents, config=config)
                if m != model:
                    logger.info("LLM served by fallback model %s (requested %s)", m, model)
                return resp
            except Exception as e:
                last_exc = e
                transient = _is_transient(e)
                if is_primary and not transient:
                    raise  # genuine error on the requested model — surface it
                logger.warning("LLM %s on %s: %s — trying next model",
                               "transient" if transient else "error", m, str(e)[:80])
                continue

        # Whole chain failed transiently this round — back off, then retry the chain.
        if attempt < max_attempts - 1:
            wait = 2 * (2 ** attempt)  # 2s, 4s, 8s
            logger.warning("All Gemini models transient; backing off %ds before round %d/%d",
                           wait, attempt + 2, max_attempts)
            await asyncio.sleep(wait)

    raise last_exc if last_exc else RuntimeError("LLM call failed with no captured error")
