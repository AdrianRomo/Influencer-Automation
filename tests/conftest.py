"""Shared pytest fixtures + env setup.

Tests must run without any real external credentials. We stub minimum
env vars that module-level code in app.* requires to import, then hand
fakeredis and a SQLite in-memory engine to anything that needs storage.
"""
from __future__ import annotations

import os
import sys

# ── Environment stubs ──────────────────────────────────────────────────────
# Set before any app.* import so module-level reads don't blow up.

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("CELERY_BROKER_URL", "memory://")
os.environ.setdefault("CELERY_RESULT_BACKEND", "cache+memory://")
os.environ.setdefault("SECRET_KEY", "test-secret-key-" + "x" * 32)
os.environ.setdefault("OPENAI_API_KEY", "sk-test-unit-tests")
os.environ.setdefault("ELEVENLABS_API_KEY", "el-test-unit-tests")
os.environ.setdefault("ELEVENLABS_VOICE_ID", "test-voice")
os.environ.setdefault("LOG_LEVEL", "WARNING")  # quiet during tests

# Ensure project root is on sys.path so `import app.*` works when running
# pytest from any CWD.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import pytest


@pytest.fixture
def fake_redis(monkeypatch):
    """Patch app.redis_client.get_redis to return a fakeredis instance."""
    import fakeredis
    client = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr("app.redis_client.get_redis", lambda: client)
    return client


@pytest.fixture
def celery_eager():
    """Run Celery tasks synchronously in the caller's process.

    Tasks are invoked via .apply() (which constructs a proper task context)
    so signals, update_state, and request attributes all behave correctly.
    """
    from app.tasks import celery_app
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True
    yield celery_app
    celery_app.conf.task_always_eager = False
    celery_app.conf.task_eager_propagates = False
