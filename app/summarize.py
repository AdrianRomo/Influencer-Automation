import os
import json
import logging
import re
from typing import TYPE_CHECKING, Any, Dict, List, Optional
from openai import OpenAI

if TYPE_CHECKING:
    from app.usage import UsageCollector

logger = logging.getLogger(__name__)

client = OpenAI()


def _extract_json(raw: str) -> str:
    """Strip common LLM artifacts (markdown fences, prose wrappers) before parsing."""
    s = raw.strip()
    # Strip ```json ... ``` or ``` ... ``` fences
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s)
        s = re.sub(r"\s*```\s*$", "", s)
    return s.strip()

# --- Tuning knobs ---
DEFAULT_TARGET_SECONDS = int(os.getenv("TTS_TARGET_SECONDS", "180"))

# Output languages
OUTPUT_LANGUAGE = os.getenv("TTS_OUTPUT_LANGUAGE", "es-MX")  # force narration language
IMAGE_PROMPT_LANGUAGE = os.getenv("IMAGE_PROMPT_LANGUAGE", "en")  # prompts for image generation

# Typical narration pacing; tune per your voice later
WPM_EN = int(os.getenv("TTS_WPM_EN", "140"))
WPM_ES = int(os.getenv("TTS_WPM_ES", "140"))

# How close to the target word count we accept (absolute words)
MIN_TOLERANCE_WORDS = int(os.getenv("TTS_WORD_TOLERANCE", "10"))
TOLERANCE_SECONDS = int(os.getenv("TTS_TOLERANCE_SECONDS", "30"))  # +/- 30s window

# Storyboard
DEFAULT_SCENES = int(os.getenv("STORYBOARD_SCENES", "8"))

from app.prompts import get as _prompt

_GENERIC_PROFILE_CONTEXT = "Content profile: General News\nTone: clear, accurate, concise"
SYSTEM_SCRIPT = _prompt("script", output_language=OUTPUT_LANGUAGE, profile_context=_GENERIC_PROFILE_CONTEXT)
SYSTEM_REWRITE = _prompt("rewrite", output_language=OUTPUT_LANGUAGE, profile_context=_GENERIC_PROFILE_CONTEXT)
SYSTEM_STORYBOARD = _prompt("storyboard")


def _normalize_scene(raw: dict, index: int) -> dict:
    """Coerce an LLM-returned scene dict into the canonical field set.

    Handles both old format (scene/image_prompt) and new format
    (scene_number/visual_prompt) so the pipeline degrades gracefully.
    """
    return {
        "scene_number": raw.get("scene_number") or raw.get("scene") or (index + 1),
        "narration": raw.get("narration", ""),
        "visual_prompt": raw.get("visual_prompt") or raw.get("image_prompt", ""),
        "on_screen_text": raw.get("on_screen_text", ""),
        "asset_type": raw.get("asset_type", "b-roll"),
        "transition": raw.get("transition", "cut"),
        "notes": raw.get("notes"),
    }


def _compute_scene_timing(scenes: list[dict], wpm: float) -> list[dict]:
    """Add start_time_estimate and duration_estimate to each scene.

    Uses calibrated WPM from voice synthesis so estimates match actual audio.
    """
    enriched = []
    start = 0.0
    for s in scenes:
        words = _count_words(s.get("narration", ""))
        duration = round((words / max(wpm, 1)) * 60.0, 1)
        enriched.append({**s, "start_time_estimate": round(start, 1), "duration_estimate": duration})
        start += duration
    return enriched


def _count_words(text: str) -> int:
    tokens = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9]+(?:'[A-Za-z]+)?", text)
    return len(tokens)


def _is_spanish(lang: Optional[str]) -> bool:
    return bool(lang) and lang.lower().startswith("es")


def _pick_wpm(output_language: Optional[str]) -> int:
    return WPM_ES if _is_spanish(output_language) else WPM_EN


def _profile_prompt_context(content_profile: Optional[dict]) -> str:
    profile = content_profile or {}
    script_policy = profile.get("script_policy") or {}
    visual_policy = profile.get("visual_policy") or {}
    tone = profile.get("tone") or {}
    audience = profile.get("audience") or {}
    analysis = profile.get("analysis_schema") or {}
    preserve = script_policy.get("preserve_terms") or []
    avoid = visual_policy.get("avoid") or []
    focus = analysis.get("focus") or []
    disclaimer = profile.get("disclaimer_text")
    return "\n".join([
        f"Content profile: {profile.get('name') or 'General News'}",
        f"Description: {profile.get('description') or 'General source-driven short-form content.'}",
        f"Tone: {', '.join(tone.get('keywords') or []) or 'clear, accurate, concise'}",
        f"Audience: {audience.get('primary') or 'general viewers'}",
        f"Terms to preserve exactly when present: {', '.join(preserve) or 'names, numbers, dates, units, product/source-specific terms'}",
        f"Factuality rule: {script_policy.get('factuality') or 'Do not add unsupported facts.'}",
        f"Disclaimer required: {'yes' if script_policy.get('disclaimer_required') else 'no'}",
        f"Disclaimer text: {disclaimer or 'none'}",
        f"Visual style: {visual_policy.get('prompt_prefix') or 'clean editorial visual'}",
        f"Visual avoid list: {', '.join(avoid) or 'text overlays, logos, watermarks'}",
        f"Analysis focus: {', '.join(focus) or 'impact_score, key_claims, audience_relevance'}",
    ])


def _estimate_seconds(word_count: int, output_language: Optional[str]) -> int:
    wpm = _pick_wpm(output_language)
    return int(round((word_count / max(wpm, 1)) * 60))


def _target_words(target_seconds: int, output_language: Optional[str]) -> int:
    wpm = _pick_wpm(output_language)
    return int(round(target_seconds * (wpm / 60.0)))


def _tolerance_words(tolerance_seconds=TOLERANCE_SECONDS, output_language=None) -> int:
    wpm = _pick_wpm(output_language)
    # words spoken in tolerance window (e.g., 30s)
    return int(round(tolerance_seconds * (wpm / 60.0)))


def _call_llm(
    system: str,
    user: str,
    model: str,
    temperature: float = 0.3,
    *,
    api_key: str | None = None,
    collector: "UsageCollector | None" = None,
    operation: str = "llm",
) -> str:
    from app.circuit_breakers import openai_breaker  # lazy import — no circular deps
    from app.metrics import llm_calls_total, observe_llm_usage
    c = OpenAI(api_key=api_key) if api_key else client
    try:
        resp = openai_breaker.call(
            c.responses.create,
            model=model,
            input=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=temperature,
            store=False,
        )
    except Exception:
        llm_calls_total.labels(provider="openai", operation=operation, outcome="error").inc()
        raise
    llm_calls_total.labels(provider="openai", operation=operation, outcome="success").inc()
    text = (resp.output_text or "").strip()

    usage = getattr(resp, "usage", None)
    in_tok = getattr(usage, "input_tokens", None) if usage else None
    out_tok = getattr(usage, "output_tokens", None) if usage else None
    try:
        observe_llm_usage("openai", model, in_tok, out_tok)
    except Exception:
        pass

    if collector is not None:
        try:
            from app.pricing import estimate_openai_llm_cost, get_llm_pricing_snapshot
            tot_tok = getattr(usage, "total_tokens", None) if usage else None
            details = getattr(usage, "input_tokens_details", None) if usage else None
            cached = getattr(details, "cached_tokens", None) if details else None
            cost = estimate_openai_llm_cost(
                model=model,
                input_tokens=in_tok or 0,
                output_tokens=out_tok or 0,
                cached_input_tokens=cached or 0,
            )
            collector.record(
                provider="openai",
                operation=operation,
                model=model,
                external_request_id=getattr(resp, "id", None),
                input_tokens=in_tok,
                output_tokens=out_tok,
                total_tokens=tot_tok,
                cached_input_tokens=cached,
                estimated_cost_usd=cost,
                pricing_snapshot=get_llm_pricing_snapshot(model),
            )
        except Exception:
            pass  # never block generation on tracking failures

    return text


def make_tts_script(
        title: str,
        body: str,
        language_hint: str | None = None,
        target_seconds: int = DEFAULT_TARGET_SECONDS,
        output_language: str = OUTPUT_LANGUAGE,
        target_words: int | None = None,
        tol_words: int | None = None,
        api_key: str | None = None,
        collector: "UsageCollector | None" = None,
        content_profile: Optional[dict] = None,
) -> str:
    """
    Returns a narration-ready script aimed at ~target_seconds, always in Spanish by default.

    Forces the LLM toward the target word count via an explicit (min, max)
    directive in the initial prompt, plus up to 3 corrective rewrite passes
    that quote the measured word count so the model can adjust directionally.
    """
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    target = target_words or _target_words(target_seconds, output_language)
    tol = tol_words or _tolerance_words(target_seconds, output_language)
    # Tight band — ±tol may be generous for short targets; ±10% or ±tol (smaller) keeps us accurate.
    tol = max(10, min(tol, int(round(target * 0.12))))
    lo, hi = target - tol, target + tol
    src_hint = language_hint or "auto-detect"
    profile_context = _profile_prompt_context(content_profile)
    system_script = _prompt(
        "script",
        output_language=output_language,
        profile_context=profile_context,
    )
    system_rewrite = _prompt(
        "rewrite",
        output_language=output_language,
        profile_context=profile_context,
    )
    prompt = f"""TITLE: {title}

ARTICLE TEXT:
{body}

Input Language (hint): {src_hint}

Output language:
- {output_language} only.

LENGTH IS A HARD REQUIREMENT:
- Write between {lo} and {hi} words (target {target}).
- Count words carefully before finalizing.
- Narration duration budget: ~{target_seconds} seconds at ~{int(round(target * 60 / max(target_seconds, 1)))} WPM.
- If the article lacks content, expand with relevant context, implications, or background from the source. Do NOT invent facts.
- If the article has too much content, compress by dropping less-essential details. Never truncate mid-sentence.
"""

    script = _call_llm(system_script, prompt, model=model, temperature=0.3, api_key=api_key,
                       collector=collector, operation="script")
    wc = _count_words(script)

    for attempt in range(3):
        if lo <= wc <= hi:
            break

        if wc > hi:
            direction, delta = "shorten", wc - target
            guidance = f"Your current script is {wc} words — REMOVE about {delta} words of the least-essential detail."
        else:
            direction, delta = "expand", target - wc
            guidance = (
                f"Your current script is only {wc} words — ADD about {delta} more words "
                "of relevant context (background, mechanism, implications, comparisons). "
                "Do NOT invent facts. Stay faithful to the source."
            )
        rewrite_prompt = f"""Rewrite the TTS script to fit the target word count and selected content profile.

TARGET: {target} words. Acceptable range: {lo} to {hi} words.
Current length: {wc} words ({direction} by ~{delta} words).

{guidance}

Rules:
- Preserve source-specific names, numbers, dates, units, and profile-listed terms exactly.
- Keep it natural spoken narration.
- Include the profile disclaimer only when the content profile requires one.
- Output ONLY the finalized script, nothing else.

SCRIPT:
{script}
"""
        script = _call_llm(system_rewrite, rewrite_prompt, model=model, temperature=0.2, api_key=api_key,
                           collector=collector, operation="rewrite")
        wc = _count_words(script)

    return script.strip()


def make_storyboard(
        title: str,
        script: str,
        language_hint: str | None = None,
        n_scenes: int = DEFAULT_SCENES,
        image_prompt_language: str = IMAGE_PROMPT_LANGUAGE,
        output_language: str = OUTPUT_LANGUAGE,
        wpm_estimate: float | None = None,
        api_key: str | None = None,
        collector: "UsageCollector | None" = None,
        content_profile: Optional[dict] = None,
) -> List[Dict[str, Any]]:
    """Return timing-enriched scene list aligned to the narration script.

    Narration stays in Spanish; visual_prompt and on_screen_text are in
    image_prompt_language (English by default) for better image-gen compatibility.
    Timing is computed in Python from word counts + WPM so it matches the actual
    voice synthesis cadence rather than relying on LLM guesses.
    """
    if n_scenes <= 0:
        return []
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    profile_context = _profile_prompt_context(content_profile)

    user = f"""
Create {n_scenes} scenes for a narrated video based on the script.

Return a JSON array where each object has exactly these fields:
- scene_number: integer starting at 1
- narration: {output_language}, 1-2 sentences directly from the script. No new facts.
- visual_prompt: {image_prompt_language} description for image/video generation. Follow the selected profile's visual style. No text overlays. No logos.
- on_screen_text: a 1-4 word {image_prompt_language} phrase to show as a text overlay on the video frame.
- asset_type: one of "b-roll", "title-card", or "outro". Use "title-card" for scene 1 and "outro" for the last scene.
- transition: always "cut" for this content type.

CONTENT PROFILE:
{profile_context}

TITLE: {title}

SCRIPT:
{script}
""".strip()

    raw = _call_llm(SYSTEM_STORYBOARD, user, model=model, temperature=0.2, api_key=api_key,
                    collector=collector, operation="storyboard")
    scenes = _parse_scenes_with_retry(
        raw, user_prompt=user, model=model, api_key=api_key, collector=collector,
    )

    wpm = wpm_estimate or float(_pick_wpm(OUTPUT_LANGUAGE))
    return _compute_scene_timing(scenes, wpm)


def _parse_scenes_with_retry(
    raw: str,
    user_prompt: str,
    model: str,
    api_key: str | None,
    collector: "UsageCollector | None",
) -> List[Dict[str, Any]]:
    """Parse storyboard JSON; on failure, retry once with a corrective prompt."""
    for attempt in range(2):
        try:
            data = json.loads(_extract_json(raw))
            if isinstance(data, list) and data:
                return [_normalize_scene(s, i) for i, s in enumerate(data)]
        except Exception as exc:
            logger.warning(
                "Storyboard JSON parse failed (attempt %d): %s; raw[:200]=%r",
                attempt + 1, exc, (raw or "")[:200],
            )
        if attempt == 0:
            correction = (
                "Your previous response was not valid JSON. Return ONLY a JSON array "
                "(no markdown fences, no prose). Each element must be an object with: "
                "scene_number, narration, visual_prompt, on_screen_text, asset_type, transition.\n\n"
                f"{user_prompt}"
            )
            raw = _call_llm(
                SYSTEM_STORYBOARD, correction, model=model, temperature=0.1,
                api_key=api_key, collector=collector, operation="storyboard_retry",
            )
    logger.error("Storyboard generation failed after retry; returning empty list")
    return []


def make_tts_bundle(
        title: str,
        body: str,
        language_hint: str | None = None,
        target_seconds: int = DEFAULT_TARGET_SECONDS,
        n_scenes: int = DEFAULT_SCENES,
        output_language: str = OUTPUT_LANGUAGE,
        target_words: int | None = None,
        tol_words: int | None = None,
        wpm_estimate: float | None = None,
        api_key: str | None = None,
        collector: "UsageCollector | None" = None,
        content_profile: Optional[dict] = None,
) -> Dict[str, Any]:
    """Convenience: script + metadata + timing-enriched storyboard in one call."""
    script = make_tts_script(
        title, body,
        language_hint=language_hint,
        target_seconds=target_seconds,
        output_language=output_language,
        target_words=target_words,
        tol_words=tol_words,
        api_key=api_key,
        collector=collector,
        content_profile=content_profile,
    )
    wc = _count_words(script)
    # Prefer the caller's calibrated WPM over the language default so the
    # estimate reflects the actual voice cadence.
    eff_wpm = wpm_estimate or float(_pick_wpm(output_language))
    est = int(round((wc / max(eff_wpm, 1)) * 60))
    scenes = make_storyboard(
        title, script,
        language_hint=language_hint,
        n_scenes=n_scenes,
        output_language=output_language,
        wpm_estimate=wpm_estimate,
        api_key=api_key,
        collector=collector,
        content_profile=content_profile,
    )
    total_duration = sum(s.get("duration_estimate", 0.0) for s in scenes)

    return {
        "script": script,
        "word_count": wc,
        "estimated_seconds": est,
        "target_seconds": target_seconds,
        "scenes": scenes,
        "total_duration_estimate": round(total_duration, 1),
        "output_language": output_language,
    }


def _words_for_seconds(seconds: int, wpm: float) -> int:
    return int(round(seconds * (wpm / 60.0)))


_PLATFORM_CAPTION_HINTS: Dict[str, str] = {
    "tiktok":         "TikTok: punchy hook in line 1, conversational, 150–220 chars, 3–5 hashtags, emojis optional",
    "reels":          "Instagram Reels: engaging opener, 150–220 chars, 5–8 hashtags, 1–2 emojis",
    "youtube_shorts": "YouTube Shorts: concise, curiosity-driven, 120–180 chars, 3–5 hashtags",
    "youtube":        "YouTube: informative title-style opener, 200–350 chars, 5–10 hashtags, no emojis required",
    "facebook":       "Facebook: conversational, slight longer form OK, 180–280 chars, 3–5 hashtags",
}

_SYSTEM_CAPTIONS = _prompt("captions", profile_context=_GENERIC_PROFILE_CONTEXT)


def generate_social_captions(
    title: str,
    script: str,
    platforms: List[str],
    output_language: str = OUTPUT_LANGUAGE,
    api_key: str | None = None,
    collector: "UsageCollector | None" = None,
    content_profile: Optional[dict] = None,
) -> Dict[str, Any]:
    """Generate platform-optimized post captions + hashtags for the given platforms.

    Returns a dict keyed by platform id, each with 'caption' and 'hashtags' keys.
    """
    if not platforms:
        return {}
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    profile_context = _profile_prompt_context(content_profile)
    system_captions = _prompt("captions", profile_context=profile_context)
    hints = "\n".join(
        f"- {pid}: {_PLATFORM_CAPTION_HINTS.get(pid, 'social media post, 150–250 chars, 3–5 hashtags')}"
        for pid in platforms
    )
    prompt = f"""Article title: {title}

Script (language: {output_language}):
{script[:2000]}

CONTENT PROFILE:
{profile_context}

Write captions for these platforms (write in the script's language: {output_language}):
{hints}

Return ONLY a JSON object with the platform IDs as keys."""
    raw = _call_llm(
        system_captions, prompt,
        model=model, temperature=0.4,
        api_key=api_key, collector=collector, operation="captions",
    )
    for attempt in range(2):
        try:
            data = json.loads(_extract_json(raw))
            if isinstance(data, dict):
                return data
        except Exception as exc:
            logger.warning(
                "Social captions JSON parse failed (attempt %d): %s; raw[:200]=%r",
                attempt + 1, exc, (raw or "")[:200],
            )
        if attempt == 0:
            correction = (
                "Your previous response was not valid JSON. Return ONLY a JSON object "
                "(no markdown, no prose) mapping each platform id to an object with "
                "`caption` and `hashtags` keys.\n\n" + prompt
            )
            raw = _call_llm(
                system_captions, correction,
                model=model, temperature=0.2,
                api_key=api_key, collector=collector, operation="captions_retry",
            )
    logger.error("Social caption generation failed after retry; returning empty dict")
    return {}


def rewrite_to_target_words(
    script: str,
    target_words: int,
    tol_words: int = 10,
    api_key: str | None = None,
    collector: "UsageCollector | None" = None,
    output_language: str = OUTPUT_LANGUAGE,
    content_profile: Optional[dict] = None,
) -> str:
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    profile_context = _profile_prompt_context(content_profile)
    system_rewrite = _prompt(
        "rewrite",
        output_language=output_language,
        profile_context=profile_context,
    )
    prompt = f"""Rewrite this TTS script to fit the word count range.

TARGET RANGE: {target_words - tol_words} to {target_words + tol_words} words (target {target_words}).
Output language: {output_language}.
Do not add new facts. Preserve source-specific names, numbers, dates, units, and profile-listed terms exactly.
Keep it natural spoken narration. Include the profile disclaimer only when required.

CONTENT PROFILE:
{profile_context}

SCRIPT:
{script}
"""
    return _call_llm(system_rewrite, prompt, model=model, temperature=0.2, api_key=api_key,
                     collector=collector, operation="rewrite")
