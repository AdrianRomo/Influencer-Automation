#!/usr/bin/env python3
"""
Pre-alembic bootstrap.

Handles deployments where the DB was (partially) created by create_all() before
alembic was introduced. Inspects the live schema and stamps alembic_version at the
highest revision already satisfied, so `alembic upgrade head` only runs what's
actually missing.

Safe to run on a fully-migrated DB (no-op) or a fresh DB (no-op; alembic creates
everything from scratch).
"""
import os
from sqlalchemy import create_engine, inspect, text

DATABASE_URL = os.environ["DATABASE_URL"]

# Ordered (revision_id → column/table predicate).
# Returns the highest revision the live schema already satisfies.
def _detect_current_rev(inspector) -> str | None:
    all_tables = set(inspector.get_table_names())

    if "articles" not in all_tables:
        return None  # Fresh DB — let alembic handle everything from 001

    articles_cols = {c["name"] for c in inspector.get_columns("articles")}

    video_cols = (
        {c["name"] for c in inspector.get_columns("video_assets")}
        if "video_assets" in all_tables else set()
    )

    checks = [
        ("001", lambda: "sources" in all_tables),
        ("002", lambda: "users" in all_tables
                        and "user_api_keys" in all_tables
                        and "user_id" in articles_cols),
        ("003", lambda: "analysis_json" in articles_cols),
        ("004", lambda: "is_pinned" in articles_cols),
        ("005", lambda: "scene_video_assets" in all_tables
                        and "render_mode" in video_cols),
        ("006", lambda: "generation_usage_events" in all_tables),
        ("007", lambda: "language" in articles_cols
                        and "selected_platforms" in articles_cols
                        and "platform" in video_cols),
        ("008", lambda: "social_captions_json" in articles_cols),
        ("009", lambda: "thumbnail_path" in articles_cols),
    ]

    stamp = None
    for rev_id, test in checks:
        if test():
            stamp = rev_id
        else:
            break
    return stamp


def run() -> None:
    engine = create_engine(DATABASE_URL)
    with engine.connect() as conn:
        inspector = inspect(engine)
        all_tables = set(inspector.get_table_names())

        has_alembic = "alembic_version" in all_tables
        has_sources = "sources" in all_tables

        if not has_sources:
            print("[bootstrap] Fresh database — alembic will create schema from scratch", flush=True)
            return

        detected = _detect_current_rev(inspector)
        if detected is None:
            print("[bootstrap] Could not detect schema revision — skipping stamp", flush=True)
            return

        if has_alembic:
            current = conn.execute(
                text("SELECT version_num FROM alembic_version LIMIT 1")
            ).scalar()
            if current == detected:
                print(f"[bootstrap] alembic_version already at {current!r} — nothing to do", flush=True)
                return
            # alembic_version is stale (e.g. stamped at 001 but schema already has 002+ cols)
            print(f"[bootstrap] Updating stale alembic_version from {current!r} to {detected!r}", flush=True)
            conn.execute(text("DELETE FROM alembic_version"))
            conn.execute(
                text("INSERT INTO alembic_version (version_num) VALUES (:rev)"),
                {"rev": detected},
            )
        else:
            print(f"[bootstrap] No alembic_version — stamping existing schema at {detected!r}", flush=True)
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS alembic_version (
                    version_num VARCHAR(32) NOT NULL,
                    CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
                )
            """))
            conn.execute(
                text("INSERT INTO alembic_version (version_num) VALUES (:rev) ON CONFLICT DO NOTHING"),
                {"rev": detected},
            )

        conn.commit()
        print(f"[bootstrap] Done — alembic will apply any revisions after {detected!r}", flush=True)


if __name__ == "__main__":
    run()
