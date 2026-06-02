"""Ad-concept generation for the catalog-to-ad pipeline.

Turns an analyzed product into a complete short-form ad creative package — hook,
headline, three script styles (ugc/demo/influencer), per-platform captions, CTA,
on-screen text, and a pipeline-compatible storyboard — then runs a compliance
pass over it.

Pure functions only (no DB): mirrors :mod:`app.summarize` / :mod:`app.analyze`.
The Celery task assembles these into ``ad_concepts`` rows. Storyboard scenes use
the exact field set the existing render pipeline already consumes, so concepts
flow into image/video generation unchanged.
"""
from __future__ import annotations

import json
import logging
import os
from typing import TYPE_CHECKING, Optional

from app.platforms import LANGUAGES
from app.prompts import get as _prompt
from app.summarize import (
    _call_llm,
    _compute_scene_timing,
    _extract_json,
    _normalize_scene,
    _pick_wpm,
)

if TYPE_CHECKING:
    from app.usage import UsageCollector

logger = logging.getLogger(__name__)

SYSTEM_CREATIVE = _prompt("ads/creative_pack")
SYSTEM_COMPLIANCE = _prompt("ads/compliance_check")

# Shape returned when creative generation fails so callers always get the keys.
_EMPTY_PACK: dict = {
    "angle": None,
    "hook": None,
    "headline": None,
    "scripts": {},
    "cta": None,
    "on_screen_text": None,
    "captions": {},
    "storyboard": [],
}


def _lang_label(language: Optional[str]) -> str:
    if not language:
        return "English"
    return LANGUAGES.get(language, language)


def _parse_json_obj(
    raw: str,
    *,
    system: str,
    user: str,
    model: str,
    api_key: Optional[str],
    collector: "UsageCollector | None",
    operation: str,
) -> dict:
    """Parse a JSON object from an LLM response; retry once with a corrective prompt."""
    for attempt in range(2):
        try:
            data = json.loads(_extract_json(raw))
            if isinstance(data, dict):
                return data
        except Exception as exc:
            logger.warning("%s JSON parse failed (attempt %d): %s", operation, attempt + 1, exc)
        if attempt == 0:
            correction = (
                "Your previous response was not valid JSON. Return ONLY a single JSON "
                "object (no markdown fences, no prose), matching the schema you were given.\n\n"
                f"{user}"
            )
            raw = _call_llm(
                system, correction, model=model, temperature=0.2,
                api_key=api_key, collector=collector, operation=f"{operation}_retry",
            )
    return {}


def _build_creative_user(
    product: dict,
    analysis: dict,
    brand_context: Optional[dict],
    *,
    goal: str,
    platforms: list[str],
    language: Optional[str],
    angle: Optional[str],
) -> str:
    lines = [
        f"GOAL: {goal}",
        f"PLATFORMS: {', '.join(platforms)}",
        f"OUTPUT LANGUAGE: {_lang_label(language)}",
        f"ANGLE FOR THIS VARIANT: {angle or 'choose the strongest from the analysis'}",
        "",
        "PRODUCT",
        f"Title: {product.get('title', '')}",
    ]
    if product.get("category"):
        lines.append(f"Category: {product['category']}")
    if product.get("price") is not None:
        lines.append(f"Price: {product['price']} {product.get('currency') or ''}".strip())
    if product.get("description"):
        lines.append(f"Description: {str(product['description'])[:2500]}")

    if analysis:
        lines.append("")
        lines.append("ANALYSIS")
        lines.append(json.dumps({
            "audience_segments": analysis.get("audience_segments"),
            "pain_points": analysis.get("pain_points"),
            "benefits": analysis.get("benefits"),
            "emotional_triggers": analysis.get("emotional_triggers"),
            "tone_keywords": analysis.get("tone_keywords"),
        }, ensure_ascii=False)[:3000])

    if brand_context:
        lines.append("")
        lines.append("BRAND VOICE")
        lines.append(json.dumps({
            "name": brand_context.get("name"),
            "tone": brand_context.get("tone"),
        }, ensure_ascii=False)[:1500])

    return "\n".join(lines)


def generate_creative_pack(
    product: dict,
    analysis: dict,
    brand_context: Optional[dict] = None,
    *,
    goal: str = "awareness",
    platforms: Optional[list[str]] = None,
    language: Optional[str] = None,
    angle: Optional[str] = None,
    wpm_estimate: Optional[float] = None,
    api_key: Optional[str] = None,
    collector: "UsageCollector | None" = None,
) -> dict:
    """Generate one ad creative package for a product. Never raises."""
    platforms = platforms or ["tiktok"]
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    user = _build_creative_user(
        product, analysis, brand_context,
        goal=goal, platforms=platforms, language=language, angle=angle,
    )

    try:
        raw = _call_llm(
            SYSTEM_CREATIVE, user, model=model, temperature=0.7,
            api_key=api_key, collector=collector, operation="ad_creative",
        )
        pack = _parse_json_obj(
            raw, system=SYSTEM_CREATIVE, user=user, model=model,
            api_key=api_key, collector=collector, operation="ad_creative",
        )
    except Exception as exc:
        logger.warning("Creative pack generation failed (non-fatal): %s", exc)
        return dict(_EMPTY_PACK)

    if not pack:
        return dict(_EMPTY_PACK)

    # Normalize + time the storyboard so it matches the render pipeline schema.
    raw_scenes = pack.get("storyboard") or []
    scenes = [_normalize_scene(s, i) for i, s in enumerate(raw_scenes) if isinstance(s, dict)]
    wpm = wpm_estimate or float(_pick_wpm(language))
    pack["storyboard"] = _compute_scene_timing(scenes, wpm)

    return {**_EMPTY_PACK, **pack}


def _prohibited_hits(pack: dict, prohibited_words: Optional[list[str]]) -> list[str]:
    """Case-insensitive scan of all generated copy for brand-prohibited words."""
    if not prohibited_words:
        return []
    blob_parts = [
        str(pack.get("hook") or ""),
        str(pack.get("headline") or ""),
        str(pack.get("cta") or ""),
        str(pack.get("on_screen_text") or ""),
        json.dumps(pack.get("scripts") or {}, ensure_ascii=False),
        json.dumps(pack.get("captions") or {}, ensure_ascii=False),
    ]
    blob = " ".join(blob_parts).lower()
    return [w for w in prohibited_words if w and w.lower() in blob]


def run_compliance(
    pack: dict,
    *,
    category: Optional[str] = None,
    prohibited_words: Optional[list[str]] = None,
    api_key: Optional[str] = None,
    collector: "UsageCollector | None" = None,
) -> dict:
    """Compliance review of a creative pack. Merges a deterministic prohibited-word
    scan with the LLM review and escalates risk to 'high' on any hard hit.

    Never raises — returns {risk, flags} with a conservative fallback.
    """
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    hits = _prohibited_hits(pack, prohibited_words)

    user = "\n".join([
        f"PRODUCT CATEGORY: {category or 'unknown'}",
        f"PROHIBITED WORDS: {', '.join(prohibited_words or []) or 'none'}",
        "",
        "AD CREATIVE",
        json.dumps({
            "hook": pack.get("hook"),
            "headline": pack.get("headline"),
            "scripts": pack.get("scripts"),
            "captions": pack.get("captions"),
            "cta": pack.get("cta"),
        }, ensure_ascii=False)[:4000],
    ])

    result = {"risk": "low", "flags": []}
    try:
        raw = _call_llm(
            SYSTEM_COMPLIANCE, user, model=model, temperature=0.0,
            api_key=api_key, collector=collector, operation="ad_compliance",
        )
        parsed = json.loads(_extract_json(raw))
        if isinstance(parsed, dict):
            result["risk"] = parsed.get("risk", "low")
            flags = parsed.get("flags")
            result["flags"] = flags if isinstance(flags, list) else []
    except Exception as exc:
        logger.warning("Compliance check failed (non-fatal): %s", exc)
        # Fail safe: unknown copy is treated as medium risk, not silently passed.
        result["risk"] = "medium"

    # Hard prohibited-word hits always force high risk + explicit flags.
    for word in hits:
        result["flags"].append({
            "text": word,
            "reason": "Brand-prohibited word present in copy.",
            "suggested_fix": f"Remove or replace '{word}'.",
        })
    if hits:
        result["risk"] = "high"

    return result
