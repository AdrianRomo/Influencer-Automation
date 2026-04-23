"""add scene_video_assets table and render_mode to video_assets

Revision ID: 005
Revises: 004
Create Date: 2026-04-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind      = op.get_bind()
    inspector = sa_inspect(bind)
    all_tables = set(inspector.get_table_names())

    # ── scene_video_assets ────────────────────────────────────────────────────
    if "scene_video_assets" not in all_tables:
        op.create_table(
            "scene_video_assets",
            sa.Column("id",               sa.String(),  nullable=False),
            sa.Column("article_id",       sa.String(),  nullable=False),
            sa.Column("image_asset_id",   sa.String(),  nullable=True),
            sa.Column("scene_number",     sa.Integer(), nullable=False),
            sa.Column("provider",         sa.String(),  nullable=False, server_default="static"),
            sa.Column("provider_job_id",  sa.String(),  nullable=True),
            sa.Column("status",           sa.String(),  nullable=False, server_default="pending"),
            sa.Column("file_path",        sa.String(),  nullable=True),
            sa.Column("duration_seconds", sa.Float(),   nullable=True),
            sa.Column("error",            sa.Text(),    nullable=True),
            sa.Column("created_at",       sa.DateTime(), nullable=False),
            sa.Column("updated_at",       sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["article_id"],     ["articles.id"],     ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["image_asset_id"], ["image_assets.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_scene_video_assets_article_scene",
            "scene_video_assets",
            ["article_id", "scene_number"],
        )

    # ── render_mode on video_assets ───────────────────────────────────────────
    video_cols = {c["name"] for c in inspector.get_columns("video_assets")} if "video_assets" in all_tables else set()
    if "render_mode" not in video_cols:
        op.add_column(
            "video_assets",
            sa.Column("render_mode", sa.String(), nullable=False, server_default="static"),
        )

    # ── Fix has_subtitles default (boolean → integer) ─────────────────────────
    # Existing rows may have stored Python True/False; PostgreSQL stores them
    # as 't'/'f' in some cases. Cast to ensure uniform 0/1 integers.
    if "video_assets" in all_tables:
        op.execute(
            "UPDATE video_assets SET has_subtitles = CASE WHEN has_subtitles::text IN ('t','true','1') THEN 1 ELSE 0 END"
        )


def downgrade() -> None:
    op.drop_index("ix_scene_video_assets_article_scene", table_name="scene_video_assets")
    op.drop_table("scene_video_assets")
    op.drop_column("video_assets", "render_mode")
