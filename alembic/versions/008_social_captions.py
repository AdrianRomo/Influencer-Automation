"""008 – social_captions_json on articles

Revision ID: 008
Revises: 007
Create Date: 2025-04-23
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    all_tables = set(inspector.get_table_names())

    if "articles" in all_tables:
        cols = {c["name"] for c in inspector.get_columns("articles")}
        if "social_captions_json" not in cols:
            op.add_column("articles", sa.Column("social_captions_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("articles", "social_captions_json")
