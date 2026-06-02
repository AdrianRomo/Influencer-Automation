"""Add name + deleted_at to catalogs.

Enables renaming a catalog (display name, falls back to source_ref) and
soft-deleting a catalog (consistent with products' soft delete). Purely
additive, nullable columns — no data loss.

Revision ID: 017
Revises: 016
"""
from alembic import op
import sqlalchemy as sa


revision = "017"
down_revision = "016"
branch_labels = None
depends_on = None


def _cols(conn, table: str) -> set[str]:
    if conn.dialect.name == "sqlite":
        return {r[1] for r in conn.execute(sa.text(f"PRAGMA table_info({table})")).fetchall()}
    return {
        r[0] for r in conn.execute(
            sa.text("SELECT column_name FROM information_schema.columns WHERE table_name=:t"),
            {"t": table},
        ).fetchall()
    }


def upgrade() -> None:
    conn = op.get_bind()
    if "catalogs" not in set(sa.inspect(conn).get_table_names()):
        return
    present = _cols(conn, "catalogs")
    with op.batch_alter_table("catalogs") as batch_op:
        if "name" not in present:
            batch_op.add_column(sa.Column("name", sa.String(), nullable=True))
        if "deleted_at" not in present:
            batch_op.add_column(sa.Column("deleted_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    conn = op.get_bind()
    if "catalogs" not in set(sa.inspect(conn).get_table_names()):
        return
    present = _cols(conn, "catalogs")
    with op.batch_alter_table("catalogs") as batch_op:
        if "deleted_at" in present:
            batch_op.drop_column("deleted_at")
        if "name" in present:
            batch_op.drop_column("name")
