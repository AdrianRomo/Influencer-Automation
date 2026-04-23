"""Provider pricing tables and cost estimation.

All costs are estimates based on public pricing pages captured at PRICING_DATE.
Costs stored in the DB are always marked estimated=True — never treat them as
authoritative billing figures; reconcile against provider dashboards.

Update _OPENAI_LLM, _OPENAI_IMAGE, and _ELEVENLABS when provider prices change.
"""
from __future__ import annotations

PRICING_DATE = "2025-04-23"
PRICING_NOTE = (
    "Costs are estimated using in-app pricing snapshots and may differ from "
    "provider billing dashboards. See app/pricing.py for the rates applied."
)

# ── OpenAI LLM — USD per 1 million tokens ─────────────────────────────────
_OPENAI_LLM: dict[str, dict[str, float]] = {
    "gpt-4o-mini": {
        "input":        0.150,
        "output":       0.600,
        "cached_input": 0.075,
    },
    "gpt-4o-mini-2024-07-18": {
        "input":        0.150,
        "output":       0.600,
        "cached_input": 0.075,
    },
    "gpt-4o": {
        "input":        2.500,
        "output":      10.000,
        "cached_input": 1.250,
    },
    "gpt-4o-2024-11-20": {
        "input":        2.500,
        "output":      10.000,
        "cached_input": 1.250,
    },
    "gpt-4-turbo": {
        "input":       10.000,
        "output":      30.000,
        "cached_input": 5.000,
    },
    "o3-mini": {
        "input":        1.100,
        "output":       4.400,
        "cached_input": 0.550,
    },
    "o4-mini": {
        "input":        1.100,
        "output":       4.400,
        "cached_input": 0.550,
    },
}
# Fallback for unknown models — generous upper bound so we don't under-report
_OPENAI_LLM_FALLBACK = {"input": 5.000, "output": 15.000, "cached_input": 2.500}

# ── OpenAI Images — USD per image ──────────────────────────────────────────
# Key format: "{quality}_{size}"
_OPENAI_IMAGE: dict[str, dict[str, float]] = {
    "dall-e-3": {
        "standard_1024x1024": 0.040,
        "standard_1024x1792": 0.080,
        "standard_1792x1024": 0.080,
        "hd_1024x1024":       0.080,
        "hd_1024x1792":       0.120,
        "hd_1792x1024":       0.120,
    },
    "dall-e-2": {
        "standard_256x256":   0.016,
        "standard_512x512":   0.018,
        "standard_1024x1024": 0.020,
    },
}

# ── ElevenLabs TTS — USD per 1 000 characters (Creator plan estimates) ────
_ELEVENLABS: dict[str, float] = {
    "eleven_multilingual_v2":  0.300,
    "eleven_multilingual_v1":  0.300,
    "eleven_monolingual_v1":   0.300,
    "eleven_turbo_v2":         0.200,
    "eleven_turbo_v2_5":       0.200,
    "eleven_flash_v2":         0.100,
    "eleven_flash_v2_5":       0.100,
}
_ELEVENLABS_FALLBACK = 0.300


# ── Public helpers ─────────────────────────────────────────────────────────

def estimate_openai_llm_cost(
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cached_input_tokens: int = 0,
) -> float:
    """Return estimated USD cost for a single OpenAI LLM call.

    Non-cached input tokens = (input_tokens - cached_input_tokens).
    Cached tokens are billed at the lower cached_input rate.
    """
    rates = _OPENAI_LLM.get(model) or _OPENAI_LLM.get(model.split("-2024")[0]) or _OPENAI_LLM_FALLBACK
    non_cached = max(input_tokens - cached_input_tokens, 0)
    cost = (
        (non_cached * rates["input"] / 1_000_000)
        + (cached_input_tokens * rates.get("cached_input", rates["input"] * 0.5) / 1_000_000)
        + (output_tokens * rates["output"] / 1_000_000)
    )
    return round(cost, 8)


def estimate_openai_image_cost(
    model: str,
    size: str,
    quality: str = "standard",
    count: int = 1,
) -> float:
    """Return estimated USD cost for a DALL-E image generation request."""
    key = f"{quality}_{size}"
    model_prices = _OPENAI_IMAGE.get(model, {})
    price_per_image = model_prices.get(key, 0.080)  # default to dall-e-3 standard 1024x1792
    return round(price_per_image * count, 8)


def estimate_elevenlabs_cost(
    model_id: str,
    character_count: int,
) -> float:
    """Return estimated USD cost for an ElevenLabs TTS request."""
    rate_per_1k = _ELEVENLABS.get(model_id, _ELEVENLABS_FALLBACK)
    return round(rate_per_1k * character_count / 1_000, 8)


def get_llm_pricing_snapshot(model: str) -> dict:
    """Return the pricing rates that were applied, for audit storage."""
    rates = _OPENAI_LLM.get(model) or _OPENAI_LLM_FALLBACK
    return {
        "pricing_date": PRICING_DATE,
        "provider": "openai",
        "model": model,
        "input_per_1m_usd": rates["input"],
        "output_per_1m_usd": rates["output"],
        "cached_input_per_1m_usd": rates.get("cached_input"),
        "is_fallback": model not in _OPENAI_LLM,
    }


def get_image_pricing_snapshot(model: str, size: str, quality: str) -> dict:
    key = f"{quality}_{size}"
    model_prices = _OPENAI_IMAGE.get(model, {})
    return {
        "pricing_date": PRICING_DATE,
        "provider": "openai",
        "model": model,
        "key": key,
        "price_per_image_usd": model_prices.get(key, 0.080),
        "is_fallback": key not in model_prices,
    }


def get_elevenlabs_pricing_snapshot(model_id: str) -> dict:
    return {
        "pricing_date": PRICING_DATE,
        "provider": "elevenlabs",
        "model_id": model_id,
        "rate_per_1k_chars_usd": _ELEVENLABS.get(model_id, _ELEVENLABS_FALLBACK),
        "is_fallback": model_id not in _ELEVENLABS,
        "plan_note": "Creator plan estimate — actual cost depends on your ElevenLabs subscription tier",
    }
