from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ContentProfile, Source


DEFAULT_CONTENT_PROFILE_ID = "medical_news"


def _profile(
    id: str,
    name: str,
    description: str,
    *,
    tone: list[str],
    audience: str,
    preserve_terms: list[str],
    visual_prefix: str,
    analysis_focus: list[str],
    disclaimer_required: bool = False,
    disclaimer_text: str | None = None,
    default_language: str = "es-MX",
    default_platforms: list[str] | None = None,
    default_target_seconds: int = 60,
    default_n_scenes: int = 8,
) -> dict[str, Any]:
    return {
        "id": id,
        "slug": id,
        "name": name,
        "description": description,
        "is_system": 1,
        "default_language": default_language,
        "default_platforms_json": default_platforms or ["tiktok"],
        "default_target_seconds": default_target_seconds,
        "default_n_scenes": default_n_scenes,
        "tone_json": {"keywords": tone},
        "audience_json": {"primary": audience},
        "script_policy_json": {
            "preserve_terms": preserve_terms,
            "factuality": "Do not add unsupported facts. Preserve named entities, numbers, dates, units, and source-specific claims.",
            "disclaimer_required": disclaimer_required,
        },
        "visual_policy_json": {
            "prompt_prefix": visual_prefix,
            "avoid": ["text overlays", "logos", "watermarks", "misleading visuals"],
        },
        "analysis_schema_json": {
            "focus": analysis_focus,
            "impact_label": "impact_score",
        },
        "disclaimer_text": disclaimer_text,
    }


DEFAULT_CONTENT_PROFILES: list[dict[str, Any]] = [
    _profile(
        "medical_news",
        "Medical News",
        "Responsible health and medical updates for public education.",
        tone=["calm", "precise", "responsible", "reassuring when appropriate"],
        audience="General viewers who want clear medical context without hype.",
        preserve_terms=["drug names", "dosages", "units", "trial names", "agency names", "dates"],
        visual_prefix="Professional photorealistic healthcare education visual, clean and informative, no gore:",
        analysis_focus=["medical_urgency", "public_health_relevance", "key_claims"],
        disclaimer_required=True,
        disclaimer_text="Esto no es consejo médico. Consulta siempre a profesionales de salud calificados.",
    ),
    _profile(
        "sports",
        "Sports",
        "Game results, athlete stories, league news, and short-form sports explainers.",
        tone=["energetic", "clear", "fair", "fan-friendly"],
        audience="Sports fans who want quick context and the key stakes.",
        preserve_terms=["team names", "player names", "scores", "records", "dates", "league names"],
        visual_prefix="Dynamic sports editorial visual, realistic action energy, broadcast-quality composition:",
        analysis_focus=["competitive_stakes", "player_team_impact", "fan_interest"],
    ),
    _profile(
        "news",
        "News",
        "General news summaries with neutral, fact-first narration.",
        tone=["neutral", "concise", "clear", "contextual"],
        audience="Viewers who need a quick, balanced overview of current events.",
        preserve_terms=["names", "locations", "dates", "numbers", "organizations", "quotes"],
        visual_prefix="Clean editorial news visual, realistic documentary style, neutral composition:",
        analysis_focus=["newsworthiness", "public_relevance", "key_facts"],
    ),
    _profile(
        "space_news",
        "Space News",
        "Space exploration, astronomy, agencies, missions, satellites, and discoveries.",
        tone=["curious", "awe-aware", "accurate", "accessible"],
        audience="Science-curious viewers who want space updates without jargon overload.",
        preserve_terms=["mission names", "spacecraft names", "agency names", "dates", "distances", "measurements"],
        visual_prefix="Cinematic space science visual, realistic spacecraft or astronomy imagery, educational style:",
        analysis_focus=["mission_relevance", "scientific_importance", "public_interest"],
    ),
    _profile(
        "tech_news",
        "Tech News",
        "Consumer tech, platforms, startups, devices, chips, software, and policy shifts.",
        tone=["sharp", "practical", "curious", "non-hype"],
        audience="Tech-aware viewers who want what changed and why it matters.",
        preserve_terms=["company names", "product names", "model names", "specs", "prices", "dates"],
        visual_prefix="Modern technology editorial visual, clean product/newsroom aesthetic, high-detail realistic style:",
        analysis_focus=["market_impact", "user_impact", "technical_significance"],
    ),
    _profile(
        "programming_news",
        "Programming News",
        "Developer tools, frameworks, languages, releases, security notices, and open source.",
        tone=["technical but accessible", "precise", "developer-practical"],
        audience="Developers who want concise context and practical implications.",
        preserve_terms=["version numbers", "package names", "APIs", "CVE IDs", "language names", "commands"],
        visual_prefix="Developer-focused technology visual, code editor and software architecture aesthetic, clean and realistic:",
        analysis_focus=["developer_impact", "migration_risk", "ecosystem_relevance"],
    ),
    _profile(
        "ai_news",
        "AI News",
        "AI product launches, research, policy, models, tools, and industry movement.",
        tone=["clear", "skeptical", "practical", "future-aware"],
        audience="Creators, builders, and operators tracking AI changes.",
        preserve_terms=["model names", "benchmarks", "company names", "dates", "pricing", "capability claims"],
        visual_prefix="Modern AI technology editorial visual, clean futuristic but grounded composition:",
        analysis_focus=["capability_change", "market_impact", "risk_or_policy_relevance"],
    ),
    _profile(
        "business_startups",
        "Business & Startups",
        "Company moves, funding, strategy, founders, markets, and business models.",
        tone=["strategic", "plainspoken", "signal-focused"],
        audience="Operators and founders who want business implications quickly.",
        preserve_terms=["company names", "funding amounts", "investor names", "dates", "metrics"],
        visual_prefix="Modern business editorial visual, realistic office/startup/news composition:",
        analysis_focus=["business_impact", "market_signal", "stakeholders"],
    ),
    _profile(
        "finance_markets",
        "Finance & Markets",
        "Market-moving updates, macro news, company earnings, and personal finance explainers.",
        tone=["careful", "plainspoken", "risk-aware", "non-promotional"],
        audience="Viewers who want financial context without investment advice.",
        preserve_terms=["tickers", "prices", "percentages", "rates", "dates", "company names"],
        visual_prefix="Financial news editorial visual, charts and market context, clean professional style:",
        analysis_focus=["market_impact", "risk_context", "key_numbers"],
        disclaimer_required=True,
        disclaimer_text="Esto es contenido informativo, no asesoría financiera.",
    ),
    _profile(
        "science_research",
        "Science Research",
        "Research papers, discoveries, experiments, institutions, and science explainers.",
        tone=["accurate", "curious", "measured", "educational"],
        audience="Science-curious viewers who want findings explained responsibly.",
        preserve_terms=["study names", "institutions", "measurements", "dates", "sample sizes", "limitations"],
        visual_prefix="High-quality science education visual, realistic lab or research imagery, clean and informative:",
        analysis_focus=["evidence_strength", "scientific_importance", "limitations"],
    ),
    _profile(
        "climate_environment",
        "Climate & Environment",
        "Climate science, energy, conservation, weather impacts, and environmental policy.",
        tone=["clear", "measured", "solutions-aware", "non-alarmist"],
        audience="Viewers who want environmental updates with context.",
        preserve_terms=["locations", "temperatures", "measurements", "dates", "agency names", "study details"],
        visual_prefix="Environmental documentary visual, realistic nature, climate, or energy imagery, informative composition:",
        analysis_focus=["environmental_impact", "community_relevance", "evidence_strength"],
    ),
    _profile(
        "gaming_esports",
        "Gaming & Esports",
        "Game releases, esports, studios, patches, platforms, and creator culture.",
        tone=["lively", "specific", "fan-aware", "clear"],
        audience="Gamers and esports fans who want quick updates.",
        preserve_terms=["game titles", "studio names", "patch versions", "player names", "event names", "dates"],
        visual_prefix="Gaming editorial visual, vivid but clean game culture aesthetic, no copyrighted logos:",
        analysis_focus=["player_impact", "community_interest", "release_or_competitive_stakes"],
    ),
    _profile(
        "entertainment_culture",
        "Entertainment & Culture",
        "Film, music, streaming, creators, celebrities, media, and cultural moments.",
        tone=["engaging", "clear", "balanced", "culture-aware"],
        audience="Viewers tracking entertainment and creator culture.",
        preserve_terms=["names", "titles", "release dates", "platform names", "awards", "quotes"],
        visual_prefix="Entertainment editorial visual, cinematic media culture composition, polished and realistic:",
        analysis_focus=["audience_interest", "cultural_relevance", "industry_impact"],
    ),
    _profile(
        "cybersecurity",
        "Cybersecurity",
        "Security alerts, breaches, vulnerabilities, privacy, and practical risk context.",
        tone=["urgent when needed", "precise", "practical", "non-sensational"],
        audience="Technical and non-technical viewers who need risk and action context.",
        preserve_terms=["CVE IDs", "product versions", "vendor names", "dates", "severity scores", "attack names"],
        visual_prefix="Cybersecurity editorial visual, realistic security operations aesthetic, clean and professional:",
        analysis_focus=["severity", "affected_users", "recommended_actions"],
    ),
    _profile(
        "crypto_web3",
        "Crypto & Web3",
        "Crypto markets, chains, protocols, regulation, security, and web3 product news.",
        tone=["skeptical", "clear", "risk-aware", "non-promotional"],
        audience="Viewers tracking crypto news who need context and risk clarity.",
        preserve_terms=["token names", "chain names", "prices", "percentages", "protocol names", "dates"],
        visual_prefix="Crypto technology editorial visual, blockchain and finance context, clean professional style:",
        analysis_focus=["market_signal", "protocol_or_policy_impact", "risk_context"],
        disclaimer_required=True,
        disclaimer_text="Esto es contenido informativo, no asesoría financiera.",
    ),
    _profile(
        "education_learning",
        "Education & Learning",
        "Education policy, learning science, edtech, schools, and practical explainers.",
        tone=["helpful", "clear", "encouraging", "practical"],
        audience="Students, parents, educators, and lifelong learners.",
        preserve_terms=["program names", "dates", "institutions", "numbers", "policy names"],
        visual_prefix="Education editorial visual, realistic classroom or learning environment, warm and clear:",
        analysis_focus=["learner_impact", "policy_or_tool_relevance", "practical_takeaways"],
    ),
    _profile(
        "travel_lifestyle",
        "Travel & Lifestyle",
        "Destinations, travel alerts, food, wellness-adjacent lifestyle, and culture tips.",
        tone=["useful", "warm", "specific", "clear"],
        audience="Viewers looking for practical lifestyle or travel context.",
        preserve_terms=["locations", "dates", "prices", "transport names", "rules", "recommendations"],
        visual_prefix="Travel and lifestyle editorial visual, realistic destination or everyday-life composition, polished:",
        analysis_focus=["viewer_usefulness", "timeliness", "planning_relevance"],
    ),
]


DEFAULT_SOURCE_PROFILE_IDS: dict[str, str] = {
    "nasa_news": "space_news",
    "nasa_tech": "space_news",
    "elpais_tech": "tech_news",
}


def profile_for_seed_source(source_id: str) -> str:
    return DEFAULT_SOURCE_PROFILE_IDS.get(source_id, DEFAULT_CONTENT_PROFILE_ID)


def seed_content_profiles(db: Session) -> None:
    """Create system content profiles and attach legacy seed sources.

    Existing user edits are preserved. We only fill missing profile/source rows
    and assign legacy sources that do not yet have a profile.
    """
    now = datetime.utcnow()
    for data in DEFAULT_CONTENT_PROFILES:
        profile = db.get(ContentProfile, data["id"])
        if not profile:
            db.add(ContentProfile(**data, created_at=now, updated_at=now))
    db.flush()

    sources = db.execute(select(Source)).scalars().all()
    for source in sources:
        if not source.content_profile_id:
            source.content_profile_id = profile_for_seed_source(source.id)
        if source.is_system is None:
            source.is_system = 1
        if source.enabled is None:
            source.enabled = 1
        if not source.validation_status:
            source.validation_status = "unchecked"
    db.commit()


def build_profile_snapshot(profile: ContentProfile | None) -> dict[str, Any]:
    if not profile:
        profile = ContentProfile(**DEFAULT_CONTENT_PROFILES[0])
    return {
        "id": profile.id,
        "slug": profile.slug,
        "name": profile.name,
        "description": profile.description,
        "default_language": profile.default_language,
        "default_platforms": profile.default_platforms_json or ["tiktok"],
        "default_target_seconds": profile.default_target_seconds,
        "default_n_scenes": profile.default_n_scenes,
        "tone": profile.tone_json or {},
        "audience": profile.audience_json or {},
        "script_policy": profile.script_policy_json or {},
        "visual_policy": profile.visual_policy_json or {},
        "analysis_schema": profile.analysis_schema_json or {},
        "disclaimer_text": profile.disclaimer_text,
    }


def default_profile_data(profile_id: str = DEFAULT_CONTENT_PROFILE_ID) -> dict[str, Any]:
    return next(
        (dict(p) for p in DEFAULT_CONTENT_PROFILES if p["id"] == profile_id),
        dict(DEFAULT_CONTENT_PROFILES[0]),
    )
