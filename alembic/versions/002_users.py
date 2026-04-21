"""add users, user_api_keys, and user_id on articles

Revision ID: 002
Revises: 001
Create Date: 2026-04-20
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
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

    op.add_column(
        "articles",
        sa.Column("user_id", sa.String(), nullable=True),
    )
    op.create_foreign_key(
        "fk_articles_user_id",
        "articles", "users",
        ["user_id"], ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_articles_user_id", "articles", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_articles_user_id", table_name="articles")
    op.drop_constraint("fk_articles_user_id", "articles", type_="foreignkey")
    op.drop_column("articles", "user_id")
    op.drop_table("user_api_keys")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
