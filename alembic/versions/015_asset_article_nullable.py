"""Make article_id nullable on asset tables.

Catalog-to-ad video/image/audio assets are sourced from a product/concept, not
an article, so article_id must allow NULL. Asset tables already carry the
workspace_id/product_id/ad_concept_id linkage columns (migration 014); this lifts
the last hard dependency on an article.

Revision ID: 015
Revises: 014
"""
from alembic import op
import sqlalchemy as sa


revision = "015"
down_revision = "014"
branch_labels = None
depends_on = None

_TABLES = ("audio_assets", "image_assets", "video_assets", "scene_video_assets")


def _tables(conn) -> set[str]:
    return set(sa.inspect(conn).get_table_names())


def upgrade() -> None:
    conn = op.get_bind()
    existing = _tables(conn)
    for table in _TABLES:
        if table not in existing:
            continue
        with op.batch_alter_table(table) as batch_op:
            batch_op.alter_column(
                "article_id",
                existing_type=sa.String(),
                nullable=True,
            )


def downgrade() -> None:
    # Best-effort: only re-tighten when no NULL article_id rows exist, otherwise
    # leave nullable (a strict NOT NULL would fail on catalog-sourced assets).
    conn = op.get_bind()
    existing = _tables(conn)
    for table in _TABLES:
        if table not in existing:
            continue
        null_count = conn.execute(
            sa.text(f"SELECT COUNT(*) FROM {table} WHERE article_id IS NULL")
        ).scalar()
        if null_count:
            continue
        with op.batch_alter_table(table) as batch_op:
            batch_op.alter_column(
                "article_id",
                existing_type=sa.String(),
                nullable=False,
            )
