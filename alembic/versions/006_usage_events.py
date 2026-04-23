"""006 – generation_usage_events table

Revision ID: 006
Revises: 005
Create Date: 2025-04-23
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    all_tables = set(inspector.get_table_names())

    if "generation_usage_events" not in all_tables:
        op.create_table(
            "generation_usage_events",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("article_id", sa.String(), sa.ForeignKey("articles.id", ondelete="SET NULL"), nullable=True),
            sa.Column("user_id", sa.String(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("video_asset_id", sa.String(), sa.ForeignKey("video_assets.id", ondelete="SET NULL"), nullable=True),
            sa.Column("provider", sa.String(50), nullable=False),
            sa.Column("operation", sa.String(100), nullable=False),
            sa.Column("model", sa.String(100), nullable=True),
            sa.Column("external_request_id", sa.String(500), nullable=True),
            sa.Column("input_tokens", sa.Integer(), nullable=True),
            sa.Column("output_tokens", sa.Integer(), nullable=True),
            sa.Column("total_tokens", sa.Integer(), nullable=True),
            sa.Column("cached_input_tokens", sa.Integer(), nullable=True),
            sa.Column("image_count", sa.Integer(), nullable=True),
            sa.Column("image_size", sa.String(50), nullable=True),
            sa.Column("image_quality", sa.String(50), nullable=True),
            sa.Column("character_count", sa.Integer(), nullable=True),
            sa.Column("estimated_cost_usd", sa.Float(), nullable=True),
            sa.Column("pricing_snapshot", sa.JSON(), nullable=True),
            sa.Column("metadata_json", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_usage_article_id", "generation_usage_events", ["article_id"])
        op.create_index("ix_usage_user_id", "generation_usage_events", ["user_id"])
        op.create_index("ix_usage_created_at", "generation_usage_events", ["created_at"])


def downgrade() -> None:
    op.drop_table("generation_usage_events")
