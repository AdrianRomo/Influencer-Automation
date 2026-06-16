from __future__ import annotations

from app.content_profiles import (
    DEFAULT_CONTENT_PROFILE_ID,
    DEFAULT_CONTENT_PROFILES,
    profile_for_seed_source,
)
from app.image_gen import _safe_prompt
from app.summarize import _profile_prompt_context


def test_default_profiles_include_requested_and_expansion_profiles():
    ids = {p["id"] for p in DEFAULT_CONTENT_PROFILES}
    requested = {
        "sports",
        "news",
        "space_news",
        "tech_news",
        "programming_news",
        "ai_news",
    }
    extra = {
        "business_startups",
        "finance_markets",
        "science_research",
        "climate_environment",
        "gaming_esports",
        "entertainment_culture",
        "cybersecurity",
        "crypto_web3",
        "education_learning",
        "travel_lifestyle",
    }
    assert DEFAULT_CONTENT_PROFILE_ID == "medical_news"
    assert requested.issubset(ids)
    assert extra.issubset(ids)


def test_legacy_sources_get_topic_specific_profiles_where_known():
    assert profile_for_seed_source("nasa_news") == "space_news"
    assert profile_for_seed_source("nasa_tech") == "space_news"
    assert profile_for_seed_source("elpais_tech") == "tech_news"
    assert profile_for_seed_source("fda_press_releases") == "medical_news"


def test_profile_context_and_image_prompt_use_selected_profile():
    tech = next(p for p in DEFAULT_CONTENT_PROFILES if p["id"] == "tech_news")
    profile = {
        "name": tech["name"],
        "description": tech["description"],
        "tone": tech["tone_json"],
        "audience": tech["audience_json"],
        "script_policy": tech["script_policy_json"],
        "visual_policy": tech["visual_policy_json"],
        "analysis_schema": tech["analysis_schema_json"],
        "disclaimer_text": tech["disclaimer_text"],
    }

    context = _profile_prompt_context(profile)
    prompt = _safe_prompt("A chip launch briefing", profile)

    assert "Tech News" in context
    assert "company names" in context
    assert "Modern technology editorial visual" in prompt
    assert "healthcare" not in prompt.lower()
