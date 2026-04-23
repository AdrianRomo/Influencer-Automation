"""add analysis_json to articles

Revision ID: 003
Revises: 002
Create Date: 2026-04-21
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa_inspect(bind).get_columns("articles")}
    if "analysis_json" not in cols:
        op.add_column("articles", sa.Column("analysis_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("articles", "analysis_json")
