from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


# ── Auth models ────────────────────────────────────────────────────────────

class RegisterReq(BaseModel):
    email: str
    password: str


class LoginReq(BaseModel):
    email: str
    password: str


class TokenResp(BaseModel):
    access_token: str
    refresh_token: Optional[str] = None
    token_type: str = "bearer"
    user_id: str
    email: str


class UserResp(BaseModel):
    id: str
    email: str
    created_at: datetime
    has_keys: bool


class UserKeysIn(BaseModel):
    openai_key: Optional[str] = None
    elevenlabs_key: Optional[str] = None
    elevenlabs_voice_id: Optional[str] = None
    elevenlabs_model_id: Optional[str] = None


class UserKeysOut(BaseModel):
    has_openai_key: bool
    has_elevenlabs_key: bool
    elevenlabs_voice_id: Optional[str] = None
    elevenlabs_model_id: Optional[str] = None


class StoryboardScene(BaseModel):
    scene_number: int
    start_time_estimate: float  # seconds from content start
    duration_estimate: float    # seconds of narration for this scene
    narration: str              # Spanish, TTS-aligned
    visual_prompt: str          # English, for image/video generation
    on_screen_text: str = ""    # 1-5 word overlay phrase
    transition: str = "cut"     # cut | fade | dissolve
    asset_type: str = "b-roll"  # b-roll | title-card | lower-third | outro
    notes: Optional[str] = None


class Storyboard(BaseModel):
    scenes: List[StoryboardScene]
    total_duration_estimate: float  # sum of scene duration_estimates


class ArticleResponse(BaseModel):
    id: str
    source_id: str
    title: str
    url: str
    published_at: Optional[datetime] = None
    created_at: datetime
    tts_script: Optional[str] = None
    script_language: Optional[str] = None
    summary_model: Optional[str] = None
    storyboard: Optional[Storyboard] = None


class GenerationResult(BaseModel):
    article_id: str
    audio_id: str
    audio_path: str
    duration_seconds: float
    word_count: int
    title: str
    url: str
    storyboard: Optional[Storyboard] = None


# --- Content package models ---

class CaptionEntry(BaseModel):
    index: int
    start_time: float   # seconds
    end_time: float     # seconds
    text: str           # narration text for this subtitle block


class VisualPromptEntry(BaseModel):
    scene_number: int
    visual_prompt: str          # English, for image/video generation
    on_screen_text: str         # overlay text
    asset_type: str             # b-roll | title-card | outro
    start_time_estimate: float  # when this scene appears in the video


class AudioAssetRef(BaseModel):
    id: str
    download_url: str
    duration_seconds: Optional[float] = None
    format: str
    word_count: Optional[int] = None
    voice_id: str
    model_id: str
    platform: Optional[str] = None


class ScriptAsset(BaseModel):
    text: str
    language: str
    word_count: int
    estimated_duration_seconds: Optional[float] = None
    model: Optional[str] = None
    target_seconds: Optional[int] = None
    platform: Optional[str] = None


class ImageAssetRef(BaseModel):
    id: str
    scene_number: int
    visual_prompt: str
    status: str


class VideoAssetRef(BaseModel):
    id: str
    download_url: str
    duration_seconds: Optional[float] = None
    width: int
    height: int
    has_subtitles: bool
    status: str
    error: Optional[str] = None
    render_mode: str = "static"   # "static" | "animated"
    platform: Optional[str] = None


class SceneVideoRef(BaseModel):
    """Status of one AI-generated scene clip in an animated render."""
    id: str
    scene_number: int
    provider: str           # "seedance" | "static" | …
    status: str             # pending | processing | ready | failed | fallback
    duration_seconds: Optional[float] = None
    error: Optional[str] = None


class AnalysisResult(BaseModel):
    sentiment: str  # positive | neutral | cautionary | urgent
    impact_score: int
    medical_urgency: str  # routine | informational | important | critical
    key_claims: List[str] = []
    audience_relevance: Optional[str] = None


class SocialCaption(BaseModel):
    platform: str
    caption: str
    hashtags: List[str] = []


class PlatformProfileOut(BaseModel):
    id: str
    name: str
    width: int
    height: int
    aspect_ratio: str
    default_duration: int
    min_duration: int
    max_duration: int
    short_form: bool
    description: str = ""


class ContentPackage(BaseModel):
    article_id: str
    title: str
    url: str
    source_id: str
    source_name: Optional[str] = None
    published_at: Optional[datetime] = None
    generated_at: datetime
    language: str = "es-MX"
    selected_platforms: Optional[List[str]] = None
    animation_prompt: Optional[str] = None
    thumbnail_url: Optional[str] = None
    script: Optional[ScriptAsset] = None
    scripts: Optional[List[ScriptAsset]] = None  # one per selected platform
    audio: Optional[AudioAssetRef] = None
    audios: Optional[List[AudioAssetRef]] = None  # one per selected platform
    storyboard: Optional[Storyboard] = None
    captions: Optional[List[CaptionEntry]] = None
    visual_prompts: Optional[List[VisualPromptEntry]] = None
    images: Optional[List[ImageAssetRef]] = None
    video: Optional[VideoAssetRef] = None
    videos: Optional[List[VideoAssetRef]] = None
    scene_videos: Optional[List[SceneVideoRef]] = None
    analysis: Optional[AnalysisResult] = None
    social_captions: Optional[List[SocialCaption]] = None
    cost_summary: Optional["CostSummary"] = None


# ── Usage / cost tracking ──────────────────────────────────────────────────

class UsageEventOut(BaseModel):
    id: str
    provider: str
    operation: str
    model: Optional[str] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    cached_input_tokens: Optional[int] = None
    character_count: Optional[int] = None
    image_count: Optional[int] = None
    image_size: Optional[str] = None
    image_quality: Optional[str] = None
    estimated_cost_usd: Optional[float] = None
    external_request_id: Optional[str] = None
    created_at: Optional[datetime] = None


class CostSummary(BaseModel):
    article_id: str
    total_estimated_usd: float
    by_provider: dict
    by_stage: dict
    total_tokens: int
    total_characters: int
    event_count: int
    events: List[UsageEventOut] = []
    pricing_note: str = ""
