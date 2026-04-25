"""Cross-router dependencies and shared helpers.

Everything here used to live in ``app/main.py``. Splitting it out lets each
router module (auth, articles, jobs, media, admin) import what it needs
without depending on the monolithic composition root.

Organized into sections:
- Settings / environment constants
- Redis-backed primitives (rate limit, refresh tokens, user-task set)
- Auth dependencies (JWT + API-key)
- Cookie helpers
- Cross-router shared dicts (in-memory idempotency)
- Rendering helpers used by multiple routers (thumbnail/package/etc.)
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from fastapi import Cookie, Depends, HTTPException, Header, Request, Response
from sqlalchemy.orm import Session

from app.auth import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    REFRESH_TOKEN_EXPIRE_SECONDS,
    decode_access_token,
)
from app.db import get_db
from app.models import Article, User
from app.redis_client import get_redis as _get_rl_redis

logger = logging.getLogger(__name__)

# ── Settings ───────────────────────────────────────────────────────────────

API_KEY = os.getenv("API_KEY", "").strip()
DEFAULT_TARGET_SECONDS = int(os.getenv("TTS_TARGET_SECONDS", "180"))
DEFAULT_SCENES = int(os.getenv("STORYBOARD_SCENES", "8"))

# In-memory idempotency: one active task per source/article. Routers mutate
# these directly (imported by reference) — fine while the API runs as a
# single process; becomes advisory as soon as there's more than one worker.
AUDIO_TASKS: dict[str, str] = {}
VIDEO_TASKS: dict[str, str] = {}


# ── Redis primitives ───────────────────────────────────────────────────────

def rate_limit(key: str, limit: int, window: int = 60) -> None:
    """Sliding-window counter in Redis. Fails open if Redis is unavailable."""
    try:
        r = _get_rl_redis()
        pipe = r.pipeline()
        pipe.incr(key)
        pipe.expire(key, window)
        count, _ = pipe.execute()
        if int(count) > limit:
            raise HTTPException(
                status_code=429,
                detail="Too many requests — please wait before retrying",
                headers={"Retry-After": str(window)},
            )
    except HTTPException:
        raise
    except Exception:
        pass


def store_task_owner(task_id: str, user_id: Optional[str]) -> None:
    """Record task→owner in Redis so we can verify on status/cancel."""
    try:
        owner = user_id or "__anon__"
        _get_rl_redis().set(f"task:{task_id}:owner", owner, ex=86400)
    except Exception:
        pass


def assert_task_owner(task_id: str, current_user: Optional[User]) -> None:
    """Raise 403 if a JWT user tries to inspect/cancel another user's task."""
    if not current_user:
        return
    try:
        owner = _get_rl_redis().get(f"task:{task_id}:owner")
        if owner and owner != "__anon__" and owner != current_user.id:
            raise HTTPException(status_code=403, detail="Forbidden")
    except HTTPException:
        raise
    except Exception:
        pass


# ── Refresh-token storage (Redis-backed, 30-day TTL) ───────────────────────

def store_refresh_token(token: str, user_id: str) -> None:
    try:
        _get_rl_redis().set(f"rt:{token}", user_id, ex=REFRESH_TOKEN_EXPIRE_SECONDS)
    except Exception:
        pass


def consume_refresh_token(token: str) -> Optional[str]:
    """Return user_id and atomically revoke the token (single-use rotation)."""
    try:
        r = _get_rl_redis()
        key = f"rt:{token}"
        user_id = r.get(key)
        if user_id:
            r.delete(key)
        return user_id
    except Exception:
        return None


def revoke_refresh_token(token: str) -> None:
    try:
        _get_rl_redis().delete(f"rt:{token}")
    except Exception:
        pass


# ── Auth cookies ───────────────────────────────────────────────────────────

def set_auth_cookies(response: Response, access_token: str, refresh_token: str) -> None:
    is_prod = bool(os.getenv("DOMAIN", "").strip())
    response.set_cookie(
        key="access_token", value=access_token,
        httponly=True, secure=is_prod, samesite="lax",
        max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60, path="/",
    )
    response.set_cookie(
        key="refresh_token", value=refresh_token,
        httponly=True, secure=is_prod, samesite="lax",
        max_age=REFRESH_TOKEN_EXPIRE_SECONDS, path="/auth/refresh",
    )


def clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(key="access_token", path="/")
    response.delete_cookie(key="refresh_token", path="/auth/refresh")


# ── Per-user task backpressure (Redis SET of active task IDs) ─────────────

MAX_CONCURRENT_TASKS = int(os.getenv("MAX_CONCURRENT_TASKS_PER_USER", "10"))


def prune_user_tasks(r, user_id: str) -> int:
    key = f"user_tasks:{user_id}"
    members = r.smembers(key)
    if not members:
        return 0
    stale = [m for m in members if not r.exists(f"task:{m}:owner")]
    if stale:
        r.srem(key, *stale)
    return r.scard(key)


def check_user_task_capacity(user_id: str) -> None:
    try:
        r = _get_rl_redis()
        active = prune_user_tasks(r, user_id)
        if active >= MAX_CONCURRENT_TASKS:
            raise HTTPException(
                status_code=429,
                detail="Too many concurrent tasks — wait for existing jobs to complete",
            )
    except HTTPException:
        raise
    except Exception:
        pass


def register_user_task(user_id: str, task_id: str) -> None:
    try:
        r = _get_rl_redis()
        key = f"user_tasks:{user_id}"
        r.sadd(key, task_id)
        r.expire(key, 86400)
    except Exception:
        pass


# ── Auth rate limiting ─────────────────────────────────────────────────────
_AUTH_WINDOW = 60
_AUTH_MAX = 10


def check_auth_rate(request: Request) -> None:
    ip = (request.client.host if request.client else None) or "unknown"
    key = f"auth_attempts:{ip}"
    try:
        r = _get_rl_redis()
        pipe = r.pipeline()
        pipe.incr(key)
        pipe.expire(key, _AUTH_WINDOW)
        count, _ = pipe.execute()
        if int(count) > _AUTH_MAX:
            raise HTTPException(
                status_code=429,
                detail=f"Too many authentication attempts — please wait {_AUTH_WINDOW}s",
            )
    except HTTPException:
        raise
    except Exception:
        pass


# ── Auth dependencies ──────────────────────────────────────────────────────

def _user_from_bearer(token: str, db: Session) -> Optional[User]:
    user_id = decode_access_token(token)
    if not user_id:
        return None
    return db.get(User, user_id)


def get_optional_user(
    authorization: str = Header(default=""),
    access_token: str = Cookie(default=""),
    db: Session = Depends(get_db),
) -> Optional[User]:
    """Return the authenticated User or None — never raises."""
    token: str | None = None
    if authorization.startswith("Bearer "):
        token = authorization[7:]
    elif access_token:
        token = access_token
    if token:
        return _user_from_bearer(token, db)
    return None


def check_api_key(
    x_api_key: str = Header(default=""),
    authorization: str = Header(default=""),
    access_token: str = Cookie(default=""),
):
    """Accept a valid Bearer JWT (header or cookie) or the server-level X-API-Key."""
    if authorization.startswith("Bearer "):
        if decode_access_token(authorization[7:]) is not None:
            return
    if access_token:
        if decode_access_token(access_token) is not None:
            return
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing auth")


def assert_article_owner(article: Article, current_user: Optional[User]) -> None:
    """Raise 403 if a JWT user tries to access another user's article."""
    if current_user and article.user_id and article.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Forbidden")


# ── User-keys resolver (shared by jobs + articles routers) ─────────────────

def resolve_user_keys(
    user: Optional[User], db: Session
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Return (openai_key, elevenlabs_key, elevenlabs_voice_id) for the user.

    Uses the per-user DEK when present; falls back to legacy master-key
    encryption for rows written before envelope encryption landed.
    """
    if not user:
        return None, None, None
    from app.auth import decrypt_api_key, decrypt_with_dek, get_or_create_dek
    from app.models import UserApiKeys
    keys = db.get(UserApiKeys, user.id)
    if not keys:
        return None, None, None
    if keys.dek_enc:
        try:
            dek = get_or_create_dek(keys)
            openai_key = decrypt_with_dek(keys.openai_key_enc, dek) if keys.openai_key_enc else None
            el_key = decrypt_with_dek(keys.elevenlabs_key_enc, dek) if keys.elevenlabs_key_enc else None
            return openai_key, el_key, keys.elevenlabs_voice_id
        except Exception as exc:
            logger.warning("DEK decrypt failed for user %s, falling back to legacy: %s", user.id, exc)
    openai_key = decrypt_api_key(keys.openai_key_enc) if keys.openai_key_enc else None
    el_key = decrypt_api_key(keys.elevenlabs_key_enc) if keys.elevenlabs_key_enc else None
    return openai_key, el_key, keys.elevenlabs_voice_id
