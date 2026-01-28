import uuid
from datetime import datetime
from sqlalchemy import String, Text, DateTime, ForeignKey, UniqueConstraint, Integer, JSON, Index, Float
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

class Base(DeclarativeBase):
    pass

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