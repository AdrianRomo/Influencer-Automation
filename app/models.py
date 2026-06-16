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
    dek_enc: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    user: Mapped["User"] = relationship(back_populates="api_keys")


class ContentProfile(Base):
    """Reusable topic/voice policy for source-driven content generation."""
    __tablename__ = "content_profiles"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )

    slug: Mapped[str] = mapped_column(String, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_system: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    default_language: Mapped[str] = mapped_column(String(20), default="es-MX", nullable=False)
    default_platforms_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    default_target_seconds: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
    default_n_scenes: Mapped[int] = mapped_column(Integer, default=8, nullable=False)

    tone_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    audience_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    script_policy_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    visual_policy_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    analysis_schema_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    disclaimer_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_content_profiles_user_slug", "user_id", "slug"),
    )


class Source(Base):
    __tablename__ = "sources"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    content_profile_id: Mapped[str | None] = mapped_column(
        ForeignKey("content_profiles.id", ondelete="SET NULL"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    rss_url: Mapped[str] = mapped_column(String, nullable=False)
    language_hint: Mapped[str | None] = mapped_column(String, nullable=True)
    category: Mapped[str | None] = mapped_column(String, nullable=True)
    is_system: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    enabled: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    validation_status: Mapped[str] = mapped_column(String(30), default="unchecked", nullable=False)
    validation_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    articles: Mapped[list["Article"]] = relationship(back_populates="source", cascade="all, delete-orphan")

class Article(Base):
    __tablename__ = "articles"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    content_profile_id: Mapped[str | None] = mapped_column(
        ForeignKey("content_profiles.id", ondelete="SET NULL"), nullable=True, index=True
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

    # Per-platform tailored scripts — each entry is
    # {script, word_count, target_seconds, estimated_duration, storyboard}.
    # The "canonical" article.tts_script mirrors one of these (longest platform)
    # so any legacy consumer keeps working.
    platform_scripts_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Generation settings (set at prepare time, drive all downstream tasks)
    language: Mapped[str] = mapped_column(String(20), default="es-MX", nullable=False)
    selected_platforms: Mapped[list | None] = mapped_column(JSON, nullable=True)  # ["tiktok", "reels"]
    animation_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    profile_snapshot_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Cover/thumbnail image for social posts
    thumbnail_path: Mapped[str | None] = mapped_column(String, nullable=True)

    # User content management
    is_pinned: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
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
    # Nullable: catalog-to-ad assets are sourced from a product/concept, not an article.
    article_id: Mapped[str | None] = mapped_column(ForeignKey("articles.id", ondelete="CASCADE"), nullable=True)

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

    # Which platform this audio targets (None = legacy single-script era)
    platform: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Catalog-to-ad linkage (None = article-sourced / legacy). Plain string refs
    # (no DB-level FK) so the column is additive and SQLite-test friendly.
    workspace_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    product_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    ad_concept_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)

    # Observability
    status: Mapped[str] = mapped_column(String, default="created", nullable=False)  # created|ready|failed
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
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
    # Nullable: catalog-to-ad assets are sourced from a product/concept, not an article.
    article_id: Mapped[str | None] = mapped_column(ForeignKey("articles.id", ondelete="CASCADE"), nullable=True)

    scene_number: Mapped[int] = mapped_column(Integer, nullable=False)
    visual_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    file_path: Mapped[str] = mapped_column(String, nullable=False)
    provider: Mapped[str] = mapped_column(String, default="openai", nullable=False)
    model: Mapped[str] = mapped_column(String, default="dall-e-3", nullable=False)
    status: Mapped[str] = mapped_column(String, default="created", nullable=False)  # created|ready|failed
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Catalog-to-ad linkage (None = article-sourced / legacy).
    workspace_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    product_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    ad_concept_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)

    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_image_assets_article_scene", "article_id", "scene_number"),
    )


class VideoAsset(Base):
    __tablename__ = "video_assets"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    # Nullable: catalog-to-ad assets are sourced from a product/concept, not an article.
    article_id: Mapped[str | None] = mapped_column(ForeignKey("articles.id", ondelete="CASCADE"), nullable=True)
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

    # Catalog-to-ad linkage (None = article-sourced / legacy).
    workspace_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    product_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    ad_concept_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)

    status: Mapped[str] = mapped_column(String, default="created", nullable=False)  # created|ready|failed
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
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

    # Catalog-to-ad linkage so usage/cost can be billed per workspace/product.
    workspace_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    product_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    ad_concept_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)

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
    # Nullable: catalog-to-ad assets are sourced from a product/concept, not an article.
    article_id: Mapped[str | None] = mapped_column(ForeignKey("articles.id", ondelete="CASCADE"), nullable=True)
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

    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_scene_video_assets_article_scene", "article_id", "scene_number"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Catalog-to-ad B2B layer
#
# These tables sit *above* the existing article→video engine. A Product is the
# new "subject" (the catalog analogue of an Article); an AdConcept is one
# generated ad variant for a product. Downstream asset tables (image/video/audio)
# already carry nullable workspace_id/product_id/ad_concept_id so the same render
# pipeline serves both article- and catalog-sourced jobs.
# ─────────────────────────────────────────────────────────────────────────────


class Workspace(Base):
    """Top-level tenant. Every brand/product/campaign/asset belongs to one."""
    __tablename__ = "workspaces"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String, nullable=False)
    owner_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    plan: Mapped[str] = mapped_column(String(50), default="beta", nullable=False)
    credits_balance: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class WorkspaceMember(Base):
    __tablename__ = "workspace_members"
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String(20), default="member", nullable=False)  # owner|admin|member
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class Brand(Base):
    """A brand profile inside a workspace — drives voice + compliance for outputs."""
    __tablename__ = "brands"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    site_url: Mapped[str | None] = mapped_column(String, nullable=True)

    tone_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    target_audience_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    brand_voice_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    prohibited_words: Mapped[list | None] = mapped_column(JSON, nullable=True)
    claim_policy_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    ai_disclosure: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    default_language: Mapped[str] = mapped_column(String(20), default="en", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class Catalog(Base):
    """An ingestion batch — one CSV upload, one connected store, one URL set."""
    __tablename__ = "catalogs"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    brand_id: Mapped[str | None] = mapped_column(
        ForeignKey("brands.id", ondelete="SET NULL"), nullable=True
    )
    # csv | url | shopify | woocommerce | merchant | meta | manual | rss
    source_type: Mapped[str] = mapped_column(String(30), nullable=False)
    source_ref: Mapped[str | None] = mapped_column(String, nullable=True)  # filename, store domain, feed url
    # User-editable display name; falls back to source_ref in the UI when unset.
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="created", nullable=False)
    item_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class Product(Base):
    """The catalog analogue of an Article — the 'subject' an ad is generated for."""
    __tablename__ = "products"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    brand_id: Mapped[str | None] = mapped_column(
        ForeignKey("brands.id", ondelete="SET NULL"), nullable=True
    )
    catalog_id: Mapped[str | None] = mapped_column(
        ForeignKey("catalogs.id", ondelete="SET NULL"), nullable=True, index=True
    )

    external_id: Mapped[str | None] = mapped_column(String, nullable=True)  # SKU / store product id
    url: Mapped[str | None] = mapped_column(String, nullable=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    currency: Mapped[str | None] = mapped_column(String(10), nullable=True)
    category: Mapped[str | None] = mapped_column(String, nullable=True)
    attributes_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    primary_image_url: Mapped[str | None] = mapped_column(String, nullable=True)

    # Output of the analyze_product task: audience / pains / benefits / angles.
    analysis_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    status: Mapped[str] = mapped_column(String(20), default="created", nullable=False)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        # Dedupe within a catalog by external id or url (mirrors article upsert).
        UniqueConstraint("catalog_id", "external_id", name="uq_product_catalog_extid"),
        Index("ix_products_workspace_created", "workspace_id", "created_at"),
    )


class ProductImage(Base):
    __tablename__ = "product_images"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    product_id: Mapped[str] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    url: Mapped[str | None] = mapped_column(String, nullable=True)
    file_path: Mapped[str | None] = mapped_column(String, nullable=True)
    is_primary: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class Campaign(Base):
    __tablename__ = "campaigns"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    brand_id: Mapped[str | None] = mapped_column(
        ForeignKey("brands.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    goal: Mapped[str] = mapped_column(String(30), default="awareness", nullable=False)  # awareness|conversion|ugc
    platforms: Mapped[list | None] = mapped_column(JSON, nullable=True)  # ["tiktok","reels"]
    product_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="created", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class AdConcept(Base):
    """One generated ad variant for a product. Reuses the storyboard schema so the
    existing render pipeline can turn it into images/video unchanged."""
    __tablename__ = "ad_concepts"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    campaign_id: Mapped[str | None] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=True, index=True
    )
    product_id: Mapped[str] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )

    variant_index: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    angle: Mapped[str | None] = mapped_column(String, nullable=True)
    hook: Mapped[str | None] = mapped_column(Text, nullable=True)
    headline: Mapped[str | None] = mapped_column(Text, nullable=True)

    # {ugc, demo, influencer} keyed scripts
    script_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # per-platform captions + hashtags
    captions_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    cta: Mapped[str | None] = mapped_column(Text, nullable=True)
    on_screen_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # same scene schema as article.storyboard_json
    storyboard_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # {risk, flags:[{text,reason,suggested_fix}]}
    compliance_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    status: Mapped[str] = mapped_column(String(20), default="created", nullable=False)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_ad_concepts_product_variant", "product_id", "variant_index"),
        Index("ix_ad_concepts_campaign", "campaign_id"),
    )


class CreditTransaction(Base):
    """Append-only credit ledger. One row per charge / grant / refund.

    ``amount`` is signed: negative = debit (charge), positive = credit (grant/refund).
    ``balance_after`` snapshots the workspace balance post-transaction for audit.
    ``idempotency_key`` (unique) makes charges/refunds safe to retry.
    """
    __tablename__ = "credit_transactions"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    amount: Mapped[int] = mapped_column(Integer, nullable=False)  # signed
    reason: Mapped[str] = mapped_column(String(50), nullable=False)  # analyze|concept|video|signup_grant|grant|refund
    ref_type: Mapped[str | None] = mapped_column(String(50), nullable=True)  # concept|campaign|product
    ref_id: Mapped[str | None] = mapped_column(String, nullable=True)
    balance_after: Mapped[int] = mapped_column(Integer, nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String, nullable=True, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_credit_txn_workspace_created", "workspace_id", "created_at"),
    )
