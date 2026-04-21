from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


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


class ScriptAsset(BaseModel):
    text: str
    language: str
    word_count: int
    estimated_duration_seconds: Optional[float] = None
    model: Optional[str] = None


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


class ContentPackage(BaseModel):
    article_id: str
    title: str
    url: str
    source_id: str
    generated_at: datetime
    script: Optional[ScriptAsset] = None
    audio: Optional[AudioAssetRef] = None
    storyboard: Optional[Storyboard] = None
    captions: Optional[List[CaptionEntry]] = None
    visual_prompts: Optional[List[VisualPromptEntry]] = None
    images: Optional[List[ImageAssetRef]] = None
    video: Optional[VideoAssetRef] = None
