"""add is_pinned to articles

Revision ID: 004
Revises: 003
Create Date: 2026-04-21
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "articles",
        sa.Column("is_pinned", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_articles_is_pinned", "articles", ["is_pinned"])


def downgrade() -> None:
    op.drop_index("ix_articles_is_pinned", table_name="articles")
    op.drop_column("articles", "is_pinned")
