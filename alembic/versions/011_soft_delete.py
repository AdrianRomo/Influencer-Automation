"""Add deleted_at to articles, audio_assets, image_assets, video_assets, scene_video_assets.

Revision ID: 011
Revises: 010
"""
from alembic import op
import sqlalchemy as sa

revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None

_TABLES = [
    "articles",
    "audio_assets",
    "image_assets",
    "video_assets",
    "scene_video_assets",
]


def _existing_cols(conn, table: str) -> set[str]:
    if conn.dialect.name == "sqlite":
        return {r[1] for r in conn.execute(sa.text(f"PRAGMA table_info({table})")).fetchall()}
    return {
        r[0]
        for r in conn.execute(
            sa.text(
                "SELECT column_name FROM information_schema.columns WHERE table_name=:t"
            ),
            {"t": table},
        ).fetchall()
    }


def upgrade() -> None:
    conn = op.get_bind()
    for table in _TABLES:
        if "deleted_at" not in _existing_cols(conn, table):
            with op.batch_alter_table(table) as batch_op:
                batch_op.add_column(sa.Column("deleted_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    for table in _TABLES:
        with op.batch_alter_table(table) as batch_op:
            batch_op.drop_column("deleted_at")
