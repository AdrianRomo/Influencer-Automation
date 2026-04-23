"""Integration test for the prepare_article Celery task.

Uses:
- SQLite in-memory via StaticPool (shared across sessions in one test)
- fakeredis via the shared fixture (monkeypatched get_redis)
- Mocked external API calls (extract, summarize) so we assert orchestration
  without hitting network

The goal is to prove the task wires up correctly and writes the expected
Article + collector rows end-to-end.
"""
from __future__ import annotations

import pytest

from app.db import engine, SessionLocal
from app.models import Base, Source


@pytest.fixture(autouse=True)
def _fresh_schema():
    """Recreate SQLite schema per test."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def source_row():
    with SessionLocal() as db:
        src = Source(
            id="test-src",
            name="Test Source",
            rss_url="https://example.com/rss",
            language_hint="en",
        )
        db.add(src)
        db.commit()
        return src.id


def test_prepare_article_creates_article_and_script(source_row, fake_redis, celery_eager, mocker):
    """End-to-end path: extract → script → storyboard → Article row persists."""
    mocker.patch(
        "app.tasks.extract_article_text",
        return_value="This is the full article body, plenty long enough to be a real article text. " * 20,
    )
    mocker.patch(
        "app.tasks.make_tts_bundle",
        return_value={
            "script": "Este es el guion en español. " * 20,
            "word_count": 160,
            "estimated_seconds": 60,
            "target_seconds": 60,
            "scenes": [
                {"scene_number": 1, "narration": "Uno.", "visual_prompt": "v", "on_screen_text": "",
                 "asset_type": "title-card", "transition": "cut",
                 "start_time_estimate": 0.0, "duration_estimate": 30.0},
                {"scene_number": 2, "narration": "Dos.", "visual_prompt": "v", "on_screen_text": "",
                 "asset_type": "outro", "transition": "cut",
                 "start_time_estimate": 30.0, "duration_estimate": 30.0},
            ],
            "total_duration_estimate": 60.0,
            "output_language": "es-MX",
        },
    )
    mocker.patch("app.tasks.generate_social_captions", return_value={})

    from app.tasks import prepare_article
    async_result = prepare_article.apply(kwargs={
        "source_id": source_row,
        "article_url": "https://example.com/article-1",
        "article_title": "Test Article",
        "article_summary": "",
        "n_scenes": 2,
        "target_seconds": 60,
        "user_id": None,
        "language": "es-MX",
        "selected_platforms": ["tiktok"],
    })
    result = async_result.get()

    assert result["article_id"]
    assert result["word_count"] > 0

    from app.models import Article
    with SessionLocal() as db:
        art = db.get(Article, result["article_id"])
        assert art is not None
        assert art.title == "Test Article"
        assert art.storyboard_json is not None
        assert len(art.storyboard_json["scenes"]) == 2
        assert art.tts_script.startswith("Este es el guion")


def test_prepare_article_dedupes_on_same_url(source_row, fake_redis, celery_eager, mocker):
    mocker.patch(
        "app.tasks.extract_article_text",
        return_value="Body. " * 200,
    )
    mocker.patch(
        "app.tasks.make_tts_bundle",
        return_value={
            "script": "x " * 200, "word_count": 200, "estimated_seconds": 60,
            "target_seconds": 60, "scenes": [], "total_duration_estimate": 0.0,
            "output_language": "es-MX",
        },
    )
    mocker.patch("app.tasks.generate_social_captions", return_value={})
    from app.tasks import prepare_article
    url = "https://example.com/duplicate"
    r1 = prepare_article.apply(kwargs={
        "source_id": source_row, "article_url": url, "article_title": "T",
    }).get()
    r2 = prepare_article.apply(kwargs={
        "source_id": source_row, "article_url": url, "article_title": "T",
    }).get()
    assert r1["article_id"] == r2["article_id"]
