"""007 – generation settings on articles + platform on video_assets

Revision ID: 007
Revises: 006
Create Date: 2025-04-23
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    all_tables = set(inspector.get_table_names())

    # articles: add language, selected_platforms, animation_prompt
    if "articles" in all_tables:
        cols = {c["name"] for c in inspector.get_columns("articles")}
        if "language" not in cols:
            op.add_column("articles", sa.Column("language", sa.String(20), nullable=False, server_default="es-MX"))
        if "selected_platforms" not in cols:
            op.add_column("articles", sa.Column("selected_platforms", sa.JSON(), nullable=True))
        if "animation_prompt" not in cols:
            op.add_column("articles", sa.Column("animation_prompt", sa.Text(), nullable=True))

    # video_assets: add platform
    if "video_assets" in all_tables:
        cols = {c["name"] for c in inspector.get_columns("video_assets")}
        if "platform" not in cols:
            op.add_column("video_assets", sa.Column("platform", sa.String(50), nullable=True))


def downgrade() -> None:
    op.drop_column("articles", "animation_prompt")
    op.drop_column("articles", "selected_platforms")
    op.drop_column("articles", "language")
    op.drop_column("video_assets", "platform")
