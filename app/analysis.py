"""Sentiment and impact analysis for profile-driven article scripts."""
from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Optional

from openai import OpenAI

if TYPE_CHECKING:
    from app.usage import UsageCollector

from app.prompts import get as _prompt
from app.summarize import _profile_prompt_context

_client: OpenAI | None = None


def _llm() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI()
    return _client


def analyze_article(
    script: str,
    api_key: Optional[str] = None,
    collector: "UsageCollector | None" = None,
    content_profile: Optional[dict] = None,
) -> dict:
    """Return sentiment/impact analysis for a TTS script.

    Never raises — returns a safe neutral stub on any failure so callers are never blocked.
    """
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    c = OpenAI(api_key=api_key) if api_key else _llm()
    profile_context = _profile_prompt_context(content_profile)
    system = _prompt("analysis", profile_context=profile_context)

    try:
        resp = c.responses.create(
            model=model,
            input=[
                {"role": "system", "content": system},
                {"role": "user", "content": script[:4000]},
            ],
            temperature=0.2,
            store=False,
        )
        raw = (resp.output_text or "").strip()

        if collector is not None:
            try:
                from app.pricing import estimate_openai_llm_cost, get_llm_pricing_snapshot
                usage = getattr(resp, "usage", None)
                in_tok = getattr(usage, "input_tokens", None) if usage else None
                out_tok = getattr(usage, "output_tokens", None) if usage else None
                tot_tok = getattr(usage, "total_tokens", None) if usage else None
                details = getattr(usage, "input_tokens_details", None) if usage else None
                cached = getattr(details, "cached_tokens", None) if details else None
                cost = estimate_openai_llm_cost(
                    model=model, input_tokens=in_tok or 0,
                    output_tokens=out_tok or 0, cached_input_tokens=cached or 0,
                )
                collector.record(
                    provider="openai",
                    operation="analysis",
                    model=model,
                    external_request_id=getattr(resp, "id", None),
                    input_tokens=in_tok, output_tokens=out_tok,
                    total_tokens=tot_tok, cached_input_tokens=cached,
                    estimated_cost_usd=cost,
                    pricing_snapshot=get_llm_pricing_snapshot(model),
                )
            except Exception:
                pass

        # Strip markdown code fences if the model ignores instructions
        if raw.startswith("```"):
            parts = raw.split("```")
            raw = parts[1].lstrip("json").strip() if len(parts) > 1 else raw

        return json.loads(raw)

    except Exception:
        profile_id = (content_profile or {}).get("id")
        return {
            "sentiment": "neutral",
            "impact_score": 5,
            "profile_relevance": "medium",
            "medical_urgency": "informational" if profile_id == "medical_news" else None,
            "key_claims": [],
            "audience_relevance": None,
        }
