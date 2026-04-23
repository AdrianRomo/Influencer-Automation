"""add users, user_api_keys, and user_id on articles

Revision ID: 002
Revises: 001
Create Date: 2026-04-20
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    all_tables = set(inspector.get_table_names())

    if "users" not in all_tables:
        op.create_table(
            "users",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("email", sa.String(), nullable=False),
            sa.Column("hashed_password", sa.String(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("email", name="uq_users_email"),
        )
        op.create_index("ix_users_email", "users", ["email"])

    if "user_api_keys" not in all_tables:
        op.create_table(
            "user_api_keys",
            sa.Column("user_id", sa.String(), nullable=False),
            sa.Column("openai_key_enc", sa.Text(), nullable=True),
            sa.Column("elevenlabs_key_enc", sa.Text(), nullable=True),
            sa.Column("elevenlabs_voice_id", sa.String(), nullable=True),
            sa.Column("elevenlabs_model_id", sa.String(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("user_id"),
        )

    articles_cols = {c["name"] for c in inspector.get_columns("articles")}

    if "user_id" not in articles_cols:
        op.add_column("articles", sa.Column("user_id", sa.String(), nullable=True))
        # Only add FK if not already present
        fks = {fk["name"] for fk in inspector.get_foreign_keys("articles")}
        if "fk_articles_user_id" not in fks:
            op.create_foreign_key(
                "fk_articles_user_id",
                "articles", "users",
                ["user_id"], ["id"],
                ondelete="SET NULL",
            )
        idxs = {ix["name"] for ix in inspector.get_indexes("articles")}
        if "ix_articles_user_id" not in idxs:
            op.create_index("ix_articles_user_id", "articles", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_articles_user_id", table_name="articles")
    op.drop_constraint("fk_articles_user_id", "articles", type_="foreignkey")
    op.drop_column("articles", "user_id")
    op.drop_table("user_api_keys")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
