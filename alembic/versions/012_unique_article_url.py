"""Partial unique index on articles(source_id, url) WHERE deleted_at IS NULL.

Closes the dedup race previously handled only by catching IntegrityError
after a race between two workers creating the same article.

Postgres and SQLite both support partial indexes (WHERE clause on CREATE
INDEX). A plain UNIQUE constraint would conflict with soft-deleted rows
and block re-creation of an article after deletion — the partial index
avoids that by scoping uniqueness to live rows only.

Revision ID: 012
Revises: 011
"""
from alembic import op
import sqlalchemy as sa


revision = "012"
down_revision = "011"
branch_labels = None
depends_on = None


INDEX_NAME = "ux_articles_source_url_live"


def _index_exists(conn, name: str) -> bool:
    if conn.dialect.name == "sqlite":
        rows = conn.execute(
            sa.text("SELECT name FROM sqlite_master WHERE type='index' AND name=:n"),
            {"n": name},
        ).fetchall()
        return bool(rows)
    rows = conn.execute(
        sa.text(
            "SELECT 1 FROM pg_indexes WHERE schemaname='public' AND indexname=:n"
        ),
        {"n": name},
    ).fetchall()
    return bool(rows)


def upgrade() -> None:
    conn = op.get_bind()
    if _index_exists(conn, INDEX_NAME):
        return
    # Drop any duplicate soft-deleted rows that would violate the new
    # index. Live rows (deleted_at IS NULL) should already be unique via
    # the existing IntegrityError-catch logic; if an earlier race created
    # duplicates, mark the older ones as soft-deleted so the index can be
    # built without data loss.
    if conn.dialect.name == "postgresql":
        conn.execute(sa.text(
            """
            WITH dup AS (
              SELECT id, ROW_NUMBER() OVER (
                PARTITION BY source_id, url
                ORDER BY created_at DESC
              ) AS rn
              FROM articles
              WHERE deleted_at IS NULL
            )
            UPDATE articles SET deleted_at = now()
            WHERE id IN (SELECT id FROM dup WHERE rn > 1);
            """
        ))
    op.create_index(
        INDEX_NAME,
        "articles",
        ["source_id", "url"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
        sqlite_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    conn = op.get_bind()
    if _index_exists(conn, INDEX_NAME):
        op.drop_index(INDEX_NAME, table_name="articles")
