"""Plain-English coordination brief for one opportunity, written by Gemini.

The model only explains facts computed by the pipeline - it never produces the
distances or dollar figures itself. Briefs are cached so each pair is generated once.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "briefs.json"  # committed: pre-generated briefs load instantly in the demo
load_dotenv(ROOT / ".env")
FALLBACK_MODELS = ["gemini-flash-latest", "gemini-3.5-flash", "gemini-flash-lite-latest"]

PROMPT = """You are helping transmission planners at two neighboring utilities decide whether to coordinate.
Using ONLY the facts below, write a short brief (max 120 words) with three labeled parts:
**Why coordinate** - what is physically/temporally close and what the utilities could share.
**Estimated value** - restate the computed savings and what drives them.
**Caveats** - the most important uncertainty (location confidence, timeline gap, estimated vs filed costs).
Do not invent numbers, places, or facts that are not below.

FACTS (JSON):
{facts}"""


def facts_for(pair, share: str, est: dict) -> dict:
    """The exact payload sent to the model (and hashed as the cache key)."""
    facts = {k: (v.item() if hasattr(v, "item") else v) for k, v in pair.to_dict().items() if k != "rank"}
    facts["what_they_could_share"] = share
    facts["estimate"] = {k: v for k, v in est.items() if k != "assumptions"}
    return facts


def available() -> bool:
    return bool(os.getenv("GEMINI_API_KEY"))


def brief(facts: dict) -> str:
    key = hashlib.sha1(json.dumps(facts, sort_keys=True, default=str).encode()).hexdigest()
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    if key in cache:
        return cache[key]

    from google import genai

    from google.genai import errors, types

    # fail fast (one retry, 15s cap) so a busy model hands off to the next one
    # instead of the SDK backing off for a minute while the demo waits
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"], http_options=types.HttpOptions(
        timeout=15_000, retry_options=types.HttpRetryOptions(attempts=2, initial_delay=1.0, max_delay=2.0)))
    # popular models get 503 "high demand" spikes; fall through to the next one
    models = [os.getenv("GEMINI_MODEL", "gemini-3.8-flash"), *FALLBACK_MODELS]
    last_error = None
    for model in dict.fromkeys(models):
        try:
            resp = client.models.generate_content(
                model=model, contents=PROMPT.format(facts=json.dumps(facts, indent=1, default=str)),
                config=types.GenerateContentConfig(thinking_config=types.ThinkingConfig(thinking_level="low")))
            break
        except Exception as exc:  # 5xx overload, 429 rate limit, timeout -> try the next model
            if isinstance(exc, errors.ClientError) and exc.code not in (404, 429):
                raise  # bad key / bad request: another model won't help
            last_error = exc
    else:
        raise last_error
    text = resp.text.strip()
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    cache[key] = text
    CACHE.write_text(json.dumps(cache, indent=1))
    return text
