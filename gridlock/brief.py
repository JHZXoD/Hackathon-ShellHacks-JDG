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
CACHE = ROOT / "data" / "cache" / "briefs.json"
load_dotenv(ROOT / ".env")
FALLBACK_MODELS = ["gemini-3.5-flash", "gemini-2.5-flash", "gemini-flash-lite-latest"]

PROMPT = """You are helping transmission planners at two neighboring utilities decide whether to coordinate.
Using ONLY the facts below, write a short brief (max 120 words) with three labeled parts:
**Why coordinate** - what is physically/temporally close and what the utilities could share.
**Estimated value** - restate the computed savings and what drives them.
**Caveats** - the most important uncertainty (location confidence, timeline gap, estimated vs filed costs).
Do not invent numbers, places, or facts that are not below.

FACTS (JSON):
{facts}"""


def available() -> bool:
    return bool(os.getenv("GEMINI_API_KEY"))


def brief(facts: dict) -> str:
    key = hashlib.sha1(json.dumps(facts, sort_keys=True, default=str).encode()).hexdigest()
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    if key in cache:
        return cache[key]

    from google import genai

    from google.genai import errors

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    # popular models get 503 "high demand" spikes; fall through to the next one
    models = [os.getenv("GEMINI_MODEL", "gemini-flash-latest"), *FALLBACK_MODELS]
    last_error = None
    for model in dict.fromkeys(models):
        try:
            resp = client.models.generate_content(
                model=model, contents=PROMPT.format(facts=json.dumps(facts, indent=1, default=str)))
            break
        except errors.ServerError as exc:
            last_error = exc
    else:
        raise last_error
    text = resp.text.strip()
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    cache[key] = text
    CACHE.write_text(json.dumps(cache, indent=1))
    return text
