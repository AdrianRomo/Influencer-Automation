"""Sentiment and impact analysis for medical article scripts."""
from __future__ import annotations

import json
import os
from typing import Optional

from openai import OpenAI

_SYSTEM = """You are a medical-content analyst. Given a narration script about a health or medical topic, analyze it and return a JSON object with exactly these fields:

{
  "sentiment": "positive" | "neutral" | "cautionary" | "urgent",
  "impact_score": <integer 1-10>,
  "medical_urgency": "routine" | "informational" | "important" | "critical",
  "key_claims": [<string>, ...],
  "audience_relevance": "<string>"
}

Definitions:
- sentiment: overall emotional tone of the content
- impact_score: 10 = major public-health news; 1 = minor routine update
- medical_urgency: routine = no action needed; informational = good to know; important = consult a doctor; critical = immediate attention
- key_claims: 2–4 short, faithful factual statements extracted from the script
- audience_relevance: one sentence describing who benefits most from this content

Return ONLY valid JSON. No markdown fences. No explanation."""

_client: OpenAI | None = None


def _llm() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI()
    return _client


def analyze_article(script: str, api_key: Optional[str] = None) -> dict:
    """Return sentiment/impact analysis for a TTS script.

    Never raises — returns a safe neutral stub on any failure so callers are never blocked.
    """
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    c = OpenAI(api_key=api_key) if api_key else _llm()

    try:
        resp = c.responses.create(
            model=model,
            input=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": script[:4000]},
            ],
            temperature=0.2,
            store=False,
        )
        raw = (resp.output_text or "").strip()

        # Strip markdown code fences if the model ignores instructions
        if raw.startswith("```"):
            parts = raw.split("```")
            raw = parts[1].lstrip("json").strip() if len(parts) > 1 else raw

        return json.loads(raw)

    except Exception:
        return {
            "sentiment": "neutral",
            "impact_score": 5,
            "medical_urgency": "informational",
            "key_claims": [],
            "audience_relevance": None,
        }
