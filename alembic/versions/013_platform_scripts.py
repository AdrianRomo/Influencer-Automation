"""Add platform_scripts_json on articles; platform on audio_assets.

Enables per-platform script + audio so each selected platform gets a script
sized to its own max_duration.

Revision ID: 013
Revises: 012
"""
from alembic import op
import sqlalchemy as sa


revision = "013"
down_revision = "012"
branch_labels = None
depends_on = None


def _cols(conn, table: str) -> set[str]:
    if conn.dialect.name == "sqlite":
        return {r[1] for r in conn.execute(sa.text(f"PRAGMA table_info({table})")).fetchall()}
    return {
        r[0] for r in conn.execute(
            sa.text(
                "SELECT column_name FROM information_schema.columns WHERE table_name=:t"
            ),
            {"t": table},
        ).fetchall()
    }


def upgrade() -> None:
    conn = op.get_bind()

    if "platform_scripts_json" not in _cols(conn, "articles"):
        with op.batch_alter_table("articles") as batch_op:
            batch_op.add_column(sa.Column("platform_scripts_json", sa.JSON(), nullable=True))

    if "platform" not in _cols(conn, "audio_assets"):
        with op.batch_alter_table("audio_assets") as batch_op:
            batch_op.add_column(sa.Column("platform", sa.String(length=50), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("audio_assets") as batch_op:
        batch_op.drop_column("platform")
    with op.batch_alter_table("articles") as batch_op:
        batch_op.drop_column("platform_scripts_json")
