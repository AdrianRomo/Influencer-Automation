"""Add thumbnail_path to articles.

Revision ID: 009
Revises: 008
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine.reflection import Inspector

revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = Inspector.from_engine(bind)
    cols = {c["name"] for c in inspector.get_columns("articles")}

    if "thumbnail_path" not in cols:
        op.add_column("articles", sa.Column("thumbnail_path", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("articles", "thumbnail_path")
