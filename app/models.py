import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import String, Text, DateTime, ForeignKey, UniqueConstraint, Integer, JSON, Index, Float
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    email: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    api_keys: Mapped[Optional["UserApiKeys"]] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )


class UserApiKeys(Base):
    """Stores per-user provider keys, AES-encrypted at rest."""
    __tablename__ = "user_api_keys"
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    openai_key_enc: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    elevenlabs_key_enc: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    elevenlabs_voice_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    elevenlabs_model_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    user: Mapped["User"] = relationship(back_populates="api_keys")


class Source(Base):
    __tablename__ = "sources"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    rss_url: Mapped[str] = mapped_column(String, nullable=False)
    language_hint: Mapped[str | None] = mapped_column(String, nullable=True)

    articles: Mapped[list["Article"]] = relationship(back_populates="source", cascade="all, delete-orphan")

class Article(Base):
    __tablename__ = "articles"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    title: Mapped[str] = mapped_column(String, nullable=False)
    url: Mapped[str] = mapped_column(String, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Extraction / summary
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    tts_script: Mapped[str | None] = mapped_column(Text, nullable=True)
    script_language: Mapped[str | None] = mapped_column(String, nullable=True)  # "en", "es", "es-MX"
    summary_model: Mapped[str | None] = mapped_column(String, nullable=True)

    # Future-proof for video/images (store scene plan)
    storyboard_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Sentiment + impact analysis output
    analysis_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Per-platform post captions + hashtags (generated after script)
    social_captions_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Generation settings (set at prepare time, drive all downstream tasks)
    language: Mapped[str] = mapped_column(String(20), default="es-MX", nullable=False)
    selected_platforms: Mapped[list | None] = mapped_column(JSON, nullable=True)  # ["tiktok", "reels"]
    animation_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)

    # User content management
    is_pinned: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    source: Mapped["Source"] = relationship(back_populates="articles")
    audio_assets: Mapped[list["AudioAsset"]] = relationship(
        back_populates="article",
        cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("source_id", "url", name="uq_article_source_url"),
        Index("ix_articles_source_created", "source_id", "created_at"),
        Index("ix_articles_published_at", "published_at"),
        Index("ix_articles_user_id", "user_id"),
    )

class AudioAsset(Base):
    __tablename__ = "audio_assets"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    article_id: Mapped[str] = mapped_column(ForeignKey("articles.id", ondelete="CASCADE"), nullable=False)

    # TTS settings
    tts_provider: Mapped[str] = mapped_column(String, default="elevenlabs", nullable=False)
    voice_id: Mapped[str] = mapped_column(String, nullable=False)
    model_id: Mapped[str] = mapped_column(String, nullable=False)
    output_format: Mapped[str] = mapped_column(String, nullable=False)

    # Timing contract
    target_seconds: Mapped[int] = mapped_column(Integer, default=180, nullable=False)
    estimated_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    word_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Output
    file_path: Mapped[str] = mapped_column(String, nullable=False)

    # Observability
    status: Mapped[str] = mapped_column(String, default="created", nullable=False)  # created|ready|failed
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    article: Mapped["Article"] = relationship(back_populates="audio_assets")

    __table_args__ = (
        Index("ix_audio_article_created", "article_id", "created_at"),
    )

class VoiceCalibration(Base):
    __tablename__ = "voice_calibration"
    voice_id: Mapped[str] = mapped_column(String, primary_key=True)
    model_id: Mapped[str] = mapped_column(String, primary_key=True)
    speed: Mapped[float] = mapped_column(Float, primary_key=True, default=1.0)

    wpm_estimate: Mapped[float] = mapped_column(Float, nullable=False, default=140.0)
    samples: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class ImageAsset(Base):
    __tablename__ = "image_assets"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    article_id: Mapped[str] = mapped_column(ForeignKey("articles.id", ondelete="CASCADE"), nullable=False)

    scene_number: Mapped[int] = mapped_column(Integer, nullable=False)
    visual_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    file_path: Mapped[str] = mapped_column(String, nullable=False)
    provider: Mapped[str] = mapped_column(String, default="openai", nullable=False)
    model: Mapped[str] = mapped_column(String, default="dall-e-3", nullable=False)
    status: Mapped[str] = mapped_column(String, default="created", nullable=False)  # created|ready|failed
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_image_assets_article_scene", "article_id", "scene_number"),
    )


class VideoAsset(Base):
    __tablename__ = "video_assets"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    article_id: Mapped[str] = mapped_column(ForeignKey("articles.id", ondelete="CASCADE"), nullable=False)
    audio_asset_id: Mapped[str | None] = mapped_column(
        ForeignKey("audio_assets.id", ondelete="SET NULL"), nullable=True
    )

    file_path: Mapped[str] = mapped_column(String, nullable=False)
    format: Mapped[str] = mapped_column(String, default="mp4", nullable=False)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    width: Mapped[int] = mapped_column(Integer, default=1080, nullable=False)
    height: Mapped[int] = mapped_column(Integer, default=1920, nullable=False)
    has_subtitles: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # "static" = slideshow of DALL-E images; "animated" = AI-generated scene clips
    render_mode: Mapped[str] = mapped_column(String, default="static", nullable=False)
    # platform profile used for output dimensions (tiktok, reels, youtube, etc.)
    platform: Mapped[str | None] = mapped_column(String(50), nullable=True)

    status: Mapped[str] = mapped_column(String, default="created", nullable=False)  # created|ready|failed
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_video_assets_article_created", "article_id", "created_at"),
    )


class GenerationUsageEvent(Base):
    """One row per provider API call; used for cost tracking and reconciliation."""
    __tablename__ = "generation_usage_events"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    article_id: Mapped[str | None] = mapped_column(
        ForeignKey("articles.id", ondelete="SET NULL"), nullable=True
    )
    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    video_asset_id: Mapped[str | None] = mapped_column(
        ForeignKey("video_assets.id", ondelete="SET NULL"), nullable=True
    )

    provider: Mapped[str] = mapped_column(String(50), nullable=False)    # openai | elevenlabs | seedance
    operation: Mapped[str] = mapped_column(String(100), nullable=False)  # script | rewrite | storyboard | analysis | image | tts
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    external_request_id: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # OpenAI LLM token counts
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cached_input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # DALL-E image fields
    image_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    image_size: Mapped[str | None] = mapped_column(String(50), nullable=True)
    image_quality: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # ElevenLabs character usage
    character_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Cost (estimated; see pricing_snapshot for the rates applied)
    estimated_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    pricing_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Extra context / debugging
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_usage_article_id", "article_id"),
        Index("ix_usage_user_id", "user_id"),
        Index("ix_usage_created_at", "created_at"),
    )


class SceneVideoAsset(Base):
    """Per-scene animated clip generated by an image-to-video provider."""
    __tablename__ = "scene_video_assets"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    article_id: Mapped[str] = mapped_column(ForeignKey("articles.id", ondelete="CASCADE"), nullable=False)
    image_asset_id: Mapped[str | None] = mapped_column(
        ForeignKey("image_assets.id", ondelete="SET NULL"), nullable=True
    )

    scene_number: Mapped[int] = mapped_column(Integer, nullable=False)
    provider: Mapped[str] = mapped_column(String, default="static", nullable=False)
    provider_job_id: Mapped[str | None] = mapped_column(String, nullable=True)

    # pending | processing | ready | failed | fallback
    status: Mapped[str] = mapped_column(String, default="pending", nullable=False)
    file_path: Mapped[str | None] = mapped_column(String, nullable=True)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_scene_video_assets_article_scene", "article_id", "scene_number"),
    )