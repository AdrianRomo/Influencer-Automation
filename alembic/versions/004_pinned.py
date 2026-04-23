"""add is_pinned to articles

Revision ID: 004
Revises: 003
Create Date: 2026-04-21
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa_inspect(bind).get_columns("articles")}
    if "is_pinned" not in cols:
        op.add_column(
            "articles",
            sa.Column("is_pinned", sa.Integer(), nullable=False, server_default="0"),
        )
        idxs = {ix["name"] for ix in sa_inspect(bind).get_indexes("articles")}
        if "ix_articles_is_pinned" not in idxs:
            op.create_index("ix_articles_is_pinned", "articles", ["is_pinned"])


def downgrade() -> None:
    op.drop_index("ix_articles_is_pinned", table_name="articles")
    op.drop_column("articles", "is_pinned")
