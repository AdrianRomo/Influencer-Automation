"""Tests for pricing estimation + pricing snapshots."""
from __future__ import annotations

import pytest

from app.pricing import (
    estimate_openai_llm_cost,
    estimate_openai_image_cost,
    estimate_elevenlabs_cost,
    get_llm_pricing_snapshot,
    get_image_pricing_snapshot,
    get_elevenlabs_pricing_snapshot,
)


# ── LLM ────────────────────────────────────────────────────────────────────

def test_llm_known_model_cost():
    # gpt-4o-mini: $0.15/1M input, $0.60/1M output
    cost = estimate_openai_llm_cost("gpt-4o-mini", input_tokens=1_000_000, output_tokens=1_000_000)
    assert cost == pytest.approx(0.75)


def test_llm_cached_tokens_are_cheaper():
    no_cache = estimate_openai_llm_cost("gpt-4o-mini", input_tokens=1000, output_tokens=0)
    with_cache = estimate_openai_llm_cost(
        "gpt-4o-mini", input_tokens=1000, cached_input_tokens=1000, output_tokens=0
    )
    assert with_cache < no_cache
    assert with_cache == pytest.approx(1000 * 0.075 / 1_000_000, abs=1e-9)


def test_llm_unknown_model_falls_back_and_flags_it():
    cost = estimate_openai_llm_cost("gpt-9-hypothetical", input_tokens=1_000_000, output_tokens=0)
    # Fallback input rate is $5.00/1M
    assert cost == pytest.approx(5.0)
    snap = get_llm_pricing_snapshot("gpt-9-hypothetical")
    assert snap["is_fallback"] is True


def test_llm_dated_model_variant_matches_base():
    """gpt-4o-mini-2024-07-18 should pick up the same rates as gpt-4o-mini."""
    base = estimate_openai_llm_cost("gpt-4o-mini", input_tokens=100000, output_tokens=50000)
    dated = estimate_openai_llm_cost("gpt-4o-mini-2024-07-18", input_tokens=100000, output_tokens=50000)
    assert base == dated


# ── Images ─────────────────────────────────────────────────────────────────

def test_image_dall_e_3_standard_1024():
    cost = estimate_openai_image_cost("dall-e-3", "1024x1024", "standard", count=1)
    assert cost == pytest.approx(0.040)


def test_image_dall_e_3_hd_portrait():
    cost = estimate_openai_image_cost("dall-e-3", "1024x1792", "hd", count=2)
    assert cost == pytest.approx(0.240)


def test_image_fallback_snapshot_flag():
    snap = get_image_pricing_snapshot("dall-e-3", "9999x9999", "standard")
    assert snap["is_fallback"] is True


# ── ElevenLabs ─────────────────────────────────────────────────────────────

def test_elevenlabs_multilingual_v2_known_rate():
    # $0.30 per 1k chars
    cost = estimate_elevenlabs_cost("eleven_multilingual_v2", 10_000)
    assert cost == pytest.approx(3.0)


def test_elevenlabs_turbo_cheaper_than_multilingual():
    multi = estimate_elevenlabs_cost("eleven_multilingual_v2", 1000)
    turbo = estimate_elevenlabs_cost("eleven_turbo_v2_5", 1000)
    assert turbo < multi


def test_elevenlabs_fallback_flag():
    snap = get_elevenlabs_pricing_snapshot("some_future_model")
    assert snap["is_fallback"] is True


def test_elevenlabs_zero_characters():
    assert estimate_elevenlabs_cost("eleven_flash_v2", 0) == 0.0
