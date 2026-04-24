"""Add per-user DEK for envelope encryption.

Revision ID: 010
Revises: 009
"""
from alembic import op
import sqlalchemy as sa

revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name == "sqlite":
        cols = {r[1] for r in conn.execute(sa.text("PRAGMA table_info(user_api_keys)")).fetchall()}
    else:
        cols = {
            r[0] for r in conn.execute(
                sa.text("SELECT column_name FROM information_schema.columns WHERE table_name='user_api_keys'")
            ).fetchall()
        }
    if "dek_enc" not in cols:
        with op.batch_alter_table("user_api_keys") as batch_op:
            batch_op.add_column(sa.Column("dek_enc", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("user_api_keys") as batch_op:
        batch_op.drop_column("dek_enc")
