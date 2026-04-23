"""Platform output profiles for video generation.

Each profile defines the canonical output dimensions, duration range, and
display metadata for a target publishing platform. Add new platforms here
without touching any other module — tasks and endpoints read from PROFILES.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class PlatformProfile:
    id: str
    name: str
    width: int
    height: int
    aspect_ratio: str          # e.g. "9:16"
    default_duration: int      # seconds — pre-fills targetSeconds
    min_duration: int
    max_duration: int
    short_form: bool           # True = TikTok/Reels/Shorts style
    description: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "width": self.width,
            "height": self.height,
            "aspect_ratio": self.aspect_ratio,
            "default_duration": self.default_duration,
            "min_duration": self.min_duration,
            "max_duration": self.max_duration,
            "short_form": self.short_form,
            "description": self.description,
        }


PROFILES: dict[str, PlatformProfile] = {
    "tiktok": PlatformProfile(
        id="tiktok",
        name="TikTok",
        width=1080,
        height=1920,
        aspect_ratio="9:16",
        default_duration=60,
        min_duration=15,
        max_duration=180,
        short_form=True,
        description="Vertical short-form video",
    ),
    "reels": PlatformProfile(
        id="reels",
        name="Instagram Reels",
        width=1080,
        height=1920,
        aspect_ratio="9:16",
        default_duration=60,
        min_duration=15,
        max_duration=90,
        short_form=True,
        description="Vertical short-form video",
    ),
    "youtube_shorts": PlatformProfile(
        id="youtube_shorts",
        name="YouTube Shorts",
        width=1080,
        height=1920,
        aspect_ratio="9:16",
        default_duration=60,
        min_duration=15,
        max_duration=60,
        short_form=True,
        description="Vertical short-form video up to 60 s",
    ),
    "youtube": PlatformProfile(
        id="youtube",
        name="YouTube",
        width=1920,
        height=1080,
        aspect_ratio="16:9",
        default_duration=180,
        min_duration=30,
        max_duration=600,
        short_form=False,
        description="Horizontal long-form video",
    ),
    "facebook": PlatformProfile(
        id="facebook",
        name="Facebook",
        width=1080,
        height=1920,
        aspect_ratio="9:16",
        default_duration=60,
        min_duration=15,
        max_duration=240,
        short_form=True,
        description="Vertical video for Facebook Feed / Reels",
    ),
}

DEFAULT_PLATFORM = "tiktok"


def get_profile(platform_id: str) -> PlatformProfile:
    """Return the platform profile, falling back to TikTok for unknown IDs."""
    return PROFILES.get(platform_id, PROFILES[DEFAULT_PLATFORM])


# ── Language options ───────────────────────────────────────────────────────
# Maps a language code (stored on Article.language) to a human-readable label.
# The first two characters of the code are sent to ElevenLabs as language_code.
# The full code is passed to OpenAI as the output_language in the script system prompt.

LANGUAGES: dict[str, str] = {
    "es-MX": "Spanish (Mexico)",
    "es":    "Spanish",
    "en-US": "English (US)",
    "en":    "English",
    "pt-BR": "Portuguese (Brazil)",
    "pt":    "Portuguese",
    "fr":    "French",
    "de":    "German",
    "it":    "Italian",
    "ja":    "Japanese",
    "ko":    "Korean",
    "zh":    "Chinese (Mandarin)",
}

DEFAULT_LANGUAGE = "es-MX"

DEFAULT_ANIMATION_PROMPT = (
    "Smooth, subtle camera movement. Slow cinematic zoom. Clean and professional."
)
