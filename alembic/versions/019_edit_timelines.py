"""Add edit_timelines table for the embedded video editor.

Revision ID: 019
Revises: 018
"""
from alembic import op
import sqlalchemy as sa


revision = "019"
down_revision = "018"
branch_labels = None
depends_on = None


def _tables(conn) -> set[str]:
    return set(sa.inspect(conn).get_table_names())


def upgrade() -> None:
    conn = op.get_bind()
    if "edit_timelines" in _tables(conn):
        return

    op.create_table(
        "edit_timelines",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("article_id", sa.String(), sa.ForeignKey("articles.id", ondelete="CASCADE"), nullable=True),
        sa.Column("edit_json", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(), nullable=False, server_default="draft"),
        sa.Column("render_provider", sa.String(), nullable=True),
        sa.Column("render_job_id", sa.String(), nullable=True),
        sa.Column("video_asset_id", sa.String(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("workspace_id", sa.String(), nullable=True),
        sa.Column("product_id", sa.String(), nullable=True),
        sa.Column("ad_concept_id", sa.String(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_edit_timelines_article", "edit_timelines", ["article_id"])
    op.create_index("ix_edit_timelines_workspace_id", "edit_timelines", ["workspace_id"])
    op.create_index("ix_edit_timelines_product_id", "edit_timelines", ["product_id"])
    op.create_index("ix_edit_timelines_ad_concept_id", "edit_timelines", ["ad_concept_id"])


def downgrade() -> None:
    conn = op.get_bind()
    if "edit_timelines" not in _tables(conn):
        return
    op.drop_table("edit_timelines")
