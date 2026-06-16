"""Add content profiles and profile-scoped RSS sources.

Revision ID: 018
Revises: 017
"""
from alembic import op
import sqlalchemy as sa


revision = "018"
down_revision = "017"
branch_labels = None
depends_on = None


def _tables(conn) -> set[str]:
    return set(sa.inspect(conn).get_table_names())


def _cols(conn, table: str) -> set[str]:
    if conn.dialect.name == "sqlite":
        return {r[1] for r in conn.execute(sa.text(f"PRAGMA table_info({table})")).fetchall()}
    return {
        r[0]
        for r in conn.execute(
            sa.text("SELECT column_name FROM information_schema.columns WHERE table_name=:t"),
            {"t": table},
        ).fetchall()
    }


def _idxs(conn, table: str) -> set[str]:
    return {idx["name"] for idx in sa.inspect(conn).get_indexes(table)}


def upgrade() -> None:
    conn = op.get_bind()
    tables = _tables(conn)

    if "content_profiles" not in tables:
        op.create_table(
            "content_profiles",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("user_id", sa.String(), nullable=True),
            sa.Column("slug", sa.String(), nullable=False),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("is_system", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("default_language", sa.String(length=20), nullable=False, server_default="es-MX"),
            sa.Column("default_platforms_json", sa.JSON(), nullable=True),
            sa.Column("default_target_seconds", sa.Integer(), nullable=False, server_default="60"),
            sa.Column("default_n_scenes", sa.Integer(), nullable=False, server_default="8"),
            sa.Column("tone_json", sa.JSON(), nullable=True),
            sa.Column("audience_json", sa.JSON(), nullable=True),
            sa.Column("script_policy_json", sa.JSON(), nullable=True),
            sa.Column("visual_policy_json", sa.JSON(), nullable=True),
            sa.Column("analysis_schema_json", sa.JSON(), nullable=True),
            sa.Column("disclaimer_text", sa.Text(), nullable=True),
            sa.Column("deleted_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_content_profiles_user_id", "content_profiles", ["user_id"])
        op.create_index("ix_content_profiles_slug", "content_profiles", ["slug"])
        op.create_index("ix_content_profiles_user_slug", "content_profiles", ["user_id", "slug"])

    if "sources" in _tables(conn):
        present = _cols(conn, "sources")
        with op.batch_alter_table("sources") as batch_op:
            if "user_id" not in present:
                batch_op.add_column(sa.Column("user_id", sa.String(), nullable=True))
                batch_op.create_foreign_key("fk_sources_user_id_users", "users", ["user_id"], ["id"], ondelete="CASCADE")
            if "content_profile_id" not in present:
                batch_op.add_column(sa.Column("content_profile_id", sa.String(), nullable=True))
                batch_op.create_foreign_key(
                    "fk_sources_content_profile_id_profiles",
                    "content_profiles",
                    ["content_profile_id"],
                    ["id"],
                    ondelete="SET NULL",
                )
            if "category" not in present:
                batch_op.add_column(sa.Column("category", sa.String(), nullable=True))
            if "is_system" not in present:
                batch_op.add_column(sa.Column("is_system", sa.Integer(), nullable=False, server_default="1"))
            if "enabled" not in present:
                batch_op.add_column(sa.Column("enabled", sa.Integer(), nullable=False, server_default="1"))
            if "validation_status" not in present:
                batch_op.add_column(sa.Column("validation_status", sa.String(length=30), nullable=False, server_default="unchecked"))
            if "validation_json" not in present:
                batch_op.add_column(sa.Column("validation_json", sa.JSON(), nullable=True))
            if "deleted_at" not in present:
                batch_op.add_column(sa.Column("deleted_at", sa.DateTime(), nullable=True))

        idxs = _idxs(conn, "sources")
        if "ix_sources_user_id" not in idxs:
            op.create_index("ix_sources_user_id", "sources", ["user_id"])
        if "ix_sources_content_profile_id" not in idxs:
            op.create_index("ix_sources_content_profile_id", "sources", ["content_profile_id"])

    if "articles" in _tables(conn):
        present = _cols(conn, "articles")
        with op.batch_alter_table("articles") as batch_op:
            if "content_profile_id" not in present:
                batch_op.add_column(sa.Column("content_profile_id", sa.String(), nullable=True))
                batch_op.create_foreign_key(
                    "fk_articles_content_profile_id_profiles",
                    "content_profiles",
                    ["content_profile_id"],
                    ["id"],
                    ondelete="SET NULL",
                )
            if "profile_snapshot_json" not in present:
                batch_op.add_column(sa.Column("profile_snapshot_json", sa.JSON(), nullable=True))

        idxs = _idxs(conn, "articles")
        if "ix_articles_content_profile_id" not in idxs:
            op.create_index("ix_articles_content_profile_id", "articles", ["content_profile_id"])


def downgrade() -> None:
    conn = op.get_bind()
    if "articles" in _tables(conn):
        present = _cols(conn, "articles")
        idxs = _idxs(conn, "articles")
        if "ix_articles_content_profile_id" in idxs:
            op.drop_index("ix_articles_content_profile_id", table_name="articles")
        with op.batch_alter_table("articles") as batch_op:
            if "profile_snapshot_json" in present:
                batch_op.drop_column("profile_snapshot_json")
            if "content_profile_id" in present:
                batch_op.drop_column("content_profile_id")

    if "sources" in _tables(conn):
        idxs = _idxs(conn, "sources")
        if "ix_sources_content_profile_id" in idxs:
            op.drop_index("ix_sources_content_profile_id", table_name="sources")
        if "ix_sources_user_id" in idxs:
            op.drop_index("ix_sources_user_id", table_name="sources")
        present = _cols(conn, "sources")
        with op.batch_alter_table("sources") as batch_op:
            for col in (
                "deleted_at",
                "validation_json",
                "validation_status",
                "enabled",
                "is_system",
                "category",
                "content_profile_id",
                "user_id",
            ):
                if col in present:
                    batch_op.drop_column(col)

    if "content_profiles" in _tables(conn):
        op.drop_index("ix_content_profiles_user_slug", table_name="content_profiles")
        op.drop_index("ix_content_profiles_slug", table_name="content_profiles")
        op.drop_index("ix_content_profiles_user_id", table_name="content_profiles")
        op.drop_table("content_profiles")
