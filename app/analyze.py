"""Product analysis for the catalog-to-ad pipeline.

Given a product (and optional brand context), returns the strategic material a
creative team needs to write ads: audience segments, pain points, benefits,
emotional triggers, objections, ad angles, tone keywords, and a claim-risk flag.

Mirrors :func:`app.analysis.analyze_article`: it never raises — on any failure it
returns a safe neutral stub so the pipeline is never blocked. The product analogue
of article sentiment analysis.
"""
from __future__ import annotations

import json
import logging
import os
from typing import TYPE_CHECKING, Optional

from app.prompts import get as _prompt
from app.summarize import _call_llm, _extract_json

if TYPE_CHECKING:
    from app.usage import UsageCollector

logger = logging.getLogger(__name__)

_SYSTEM = _prompt("ads/product_analysis")

# Conservative stub returned when generation/parsing fails. Shaped like a real
# result so downstream consumers can rely on the keys existing.
_STUB: dict = {
    "audience_segments": [],
    "pain_points": [],
    "benefits": [],
    "emotional_triggers": [],
    "objections": [],
    "ad_angles": [],
    "tone_keywords": [],
    "claim_risk": "medium",
}


def _build_user_content(product: dict, brand_context: Optional[dict]) -> str:
    """Render the product (+ optional brand context) into the LLM user message."""
    lines = ["PRODUCT", f"Title: {product.get('title', '')}"]
    if product.get("category"):
        lines.append(f"Category: {product['category']}")
    if product.get("price") is not None:
        currency = product.get("currency") or ""
        lines.append(f"Price: {product['price']} {currency}".strip())
    if product.get("description"):
        lines.append(f"Description: {str(product['description'])[:4000]}")
    attrs = product.get("attributes") or {}
    if attrs:
        rendered = "; ".join(f"{k}: {v}" for k, v in list(attrs.items())[:30])
        lines.append(f"Attributes: {rendered[:2000]}")

    if brand_context:
        lines.append("")
        lines.append("BRAND CONTEXT")
        if brand_context.get("name"):
            lines.append(f"Brand: {brand_context['name']}")
        if brand_context.get("tone"):
            lines.append(f"Tone: {json.dumps(brand_context['tone'])[:1000]}")
        if brand_context.get("target_audience"):
            lines.append(f"Target audience: {json.dumps(brand_context['target_audience'])[:1000]}")

    return "\n".join(lines)


def analyze_product(
    product: dict,
    brand_context: Optional[dict] = None,
    *,
    api_key: Optional[str] = None,
    collector: "UsageCollector | None" = None,
) -> dict:
    """Return strategic ad-analysis for a single product.

    ``product`` keys used: title, description, price, currency, category, attributes.
    ``brand_context`` (optional) keys: name, tone, target_audience.

    Never raises — returns a safe stub on any failure.
    """
    if not product.get("title"):
        return dict(_STUB)

    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    user_content = _build_user_content(product, brand_context)

    try:
        raw = _call_llm(
            system=_SYSTEM,
            user=user_content,
            model=model,
            temperature=0.4,
            api_key=api_key,
            collector=collector,
            operation="product_analysis",
        )
        parsed = json.loads(_extract_json(raw))
        if not isinstance(parsed, dict):
            raise ValueError("Analysis did not return a JSON object")
        # Merge onto the stub so every expected key is always present.
        return {**_STUB, **parsed}
    except Exception as exc:
        logger.warning("Product analysis failed (non-fatal): %s", exc)
        return dict(_STUB)
