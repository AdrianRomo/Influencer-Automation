"""baseline — full schema as of initial video pipeline build

Revision ID: 001
Revises:
Create Date: 2026-04-20
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sources",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("rss_url", sa.String(), nullable=False),
        sa.Column("language_hint", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "articles",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("source_id", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("url", sa.String(), nullable=False),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.Column("raw_text", sa.Text(), nullable=True),
        sa.Column("tts_script", sa.Text(), nullable=True),
        sa.Column("script_language", sa.String(), nullable=True),
        sa.Column("summary_model", sa.String(), nullable=True),
        sa.Column("storyboard_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_id", "url", name="uq_article_source_url"),
    )
    op.create_index("ix_articles_source_created", "articles", ["source_id", "created_at"])
    op.create_index("ix_articles_published_at", "articles", ["published_at"])

    op.create_table(
        "voice_calibration",
        sa.Column("voice_id", sa.String(), nullable=False),
        sa.Column("model_id", sa.String(), nullable=False),
        sa.Column("speed", sa.Float(), nullable=False),
        sa.Column("wpm_estimate", sa.Float(), nullable=False),
        sa.Column("samples", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("voice_id", "model_id", "speed"),
    )
    op.create_table(
        "audio_assets",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("article_id", sa.String(), nullable=False),
        sa.Column("tts_provider", sa.String(), nullable=False),
        sa.Column("voice_id", sa.String(), nullable=False),
        sa.Column("model_id", sa.String(), nullable=False),
        sa.Column("output_format", sa.String(), nullable=False),
        sa.Column("target_seconds", sa.Integer(), nullable=False),
        sa.Column("estimated_seconds", sa.Integer(), nullable=True),
        sa.Column("word_count", sa.Integer(), nullable=True),
        sa.Column("file_path", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["article_id"], ["articles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audio_article_created", "audio_assets", ["article_id", "created_at"])

    op.create_table(
        "image_assets",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("article_id", sa.String(), nullable=False),
        sa.Column("scene_number", sa.Integer(), nullable=False),
        sa.Column("visual_prompt", sa.Text(), nullable=False),
        sa.Column("file_path", sa.String(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["article_id"], ["articles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_image_assets_article_scene", "image_assets", ["article_id", "scene_number"])

    op.create_table(
        "video_assets",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("article_id", sa.String(), nullable=False),
        sa.Column("audio_asset_id", sa.String(), nullable=True),
        sa.Column("file_path", sa.String(), nullable=False),
        sa.Column("format", sa.String(), nullable=False),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("has_subtitles", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["article_id"], ["articles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["audio_asset_id"], ["audio_assets.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_video_assets_article_created", "video_assets", ["article_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_video_assets_article_created", table_name="video_assets")
    op.drop_table("video_assets")
    op.drop_index("ix_image_assets_article_scene", table_name="image_assets")
    op.drop_table("image_assets")
    op.drop_index("ix_audio_article_created", table_name="audio_assets")
    op.drop_table("audio_assets")
    op.drop_table("voice_calibration")
    op.drop_index("ix_articles_published_at", table_name="articles")
    op.drop_index("ix_articles_source_created", table_name="articles")
    op.drop_table("articles")
    op.drop_table("sources")
