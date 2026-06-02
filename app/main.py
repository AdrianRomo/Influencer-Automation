from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import re
import zipfile
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime
from time import time as _time
import feedparser as _fp
from fastapi import Cookie, FastAPI, Depends, File, HTTPException, Header, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from celery.result import AsyncResult
from sqlalchemy import select, func, delete
from sqlalchemy.orm import Session
from typing import Literal, Optional

from app.redis_client import get_redis as _get_rl_redis
from app.logging_config import (
    configure_logging,
    set_correlation_id,
    set_log_context,
    get_correlation_id,
)
from app.sentry_init import init_sentry

init_sentry()
configure_logging()
logger = logging.getLogger(__name__)


from app.auth import (
    create_access_token, decode_access_token,
    hash_password, verify_password,
    encrypt_api_key, decrypt_api_key,
    get_or_create_dek, encrypt_with_dek, decrypt_with_dek,
    generate_refresh_token, REFRESH_TOKEN_EXPIRE_SECONDS, ACCESS_TOKEN_EXPIRE_MINUTES,
)
from app.db import get_db, engine
from app.models import Base, Source, AudioAsset, Article, ImageAsset, VideoAsset, SceneVideoAsset, User, UserApiKeys
from app.rss_sources import SOURCES
from app.schemas import (
    ArticleResponse, Storyboard,
    AudioAssetRef, ScriptAsset, VisualPromptEntry, ContentPackage,
    ImageAssetRef, VideoAssetRef, SceneVideoRef, AnalysisResult,
    RegisterReq, LoginReq, TokenResp, UserResp, UserKeysIn, UserKeysOut,
    CostSummary, UsageEventOut, PlatformProfileOut, SocialCaption,
)
from app.platforms import PROFILES, LANGUAGES, DEFAULT_PLATFORM, DEFAULT_LANGUAGE, DEFAULT_ANIMATION_PROMPT
from app.captions import storyboard_to_captions, captions_to_srt, captions_to_vtt
from app.summarize import _count_words, _estimate_seconds
from app.tasks import celery_app
from app.deps import (
    API_KEY,
    AUDIO_TASKS as _audio_tasks,
    DEFAULT_SCENES,
    DEFAULT_TARGET_SECONDS,
    MAX_CONCURRENT_TASKS as _MAX_CONCURRENT_TASKS,
    VIDEO_TASKS as _video_tasks,
    assert_article_owner as _assert_article_owner,
    assert_task_owner as _assert_task_owner,
    check_api_key,
    check_auth_rate as _check_auth_rate,
    check_user_task_capacity as _check_user_task_capacity,
    clear_auth_cookies as _clear_auth_cookies,
    consume_refresh_token as _consume_refresh_token,
    get_optional_user,
    rate_limit as _rate_limit,
    register_user_task as _register_user_task,
    resolve_user_keys as _resolve_user_keys,
    revoke_refresh_token as _revoke_refresh_token,
    set_auth_cookies as _set_auth_cookies,
    store_refresh_token as _store_refresh_token,
    store_task_owner as _store_task_owner,
)
from app.routers import auth as auth_router
from app.routers import health as health_router
from app.routers import media as media_router
from app.routers import workspaces as workspaces_router
from app.routers import brands as brands_router
from app.routers import catalog as catalog_router
from app.routers import products as products_router
from app.routers import campaigns as campaigns_router
from app.routers import concepts as concepts_router
from app.routers import billing as billing_router

Base.metadata.create_all(bind=engine)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure data directories exist before any request is served
    for d in [
        os.getenv("AUDIO_DIR", "/data/audio"),
        os.getenv("IMAGE_DIR", "/data/images"),
        os.getenv("VIDEO_DIR", "/data/video"),
    ]:
        os.makedirs(d, exist_ok=True)

    from sqlalchemy.orm import Session
    with Session(engine) as db:
        for s in SOURCES:
            if not db.get(Source, s["id"]):
                db.add(Source(**s))
        db.commit()
    yield


app = FastAPI(title="Medical Content Generator", lifespan=lifespan)

_cors_origins = ["http://localhost:5173", "http://127.0.0.1:5173"]
_domain = os.getenv("DOMAIN", "").strip()
if _domain:
    for host in ("autonarrator", "influencer"):
        _cors_origins.append(f"https://{host}.{_domain}")
        _cors_origins.append(f"http://{host}.{_domain}")

# Allow any subdomain of the configured DOMAIN over http/https — tolerates
# alt hostnames (www., staging., etc.) without code changes.
_cors_regex = (
    rf"^https?://([a-zA-Z0-9-]+\.)?{re.escape(_domain)}$"
    if _domain else None
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_origin_regex=_cors_regex,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-API-Key", "X-Correlation-ID"],
)

# ── Split routers ─────────────────────────────────────────────────────────
# See app/routers/__init__.py for the migration status.
app.include_router(health_router.router)
app.include_router(auth_router.router)
app.include_router(media_router.router)

# Catalog-to-ad B2B layer (workspace-scoped, JWT-required).
app.include_router(workspaces_router.router)
app.include_router(brands_router.router)
app.include_router(catalog_router.router)
app.include_router(products_router.router)
app.include_router(campaigns_router.router)
app.include_router(concepts_router.router)
app.include_router(billing_router.router)


@app.middleware("http")
async def _security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    return response


@app.middleware("http")
async def _correlation_id_middleware(request: Request, call_next):
    """Assign or propagate a correlation ID for every request.

    Clients can supply X-Correlation-ID to tie upstream traces to this request;
    otherwise a random one is generated. The ID is echoed back in the response
    so a browser user can report it for support escalation.
    """
    incoming = request.headers.get("x-correlation-id", "").strip()
    cid = set_correlation_id(incoming or None)
    try:
        response = await call_next(request)
    finally:
        # Reset per-request user/article context so it doesn't bleed into the
        # next request handled by this worker.
        set_log_context(user_id="", article_id="", task_id="")
    response.headers["X-Correlation-ID"] = cid
    return response


@app.middleware("http")
async def _metrics_middleware(request: Request, call_next):
    """Record request latency and status-class counts.

    Excludes /metrics itself to avoid scrape-loop noise, and uses the
    route-template path so cardinality stays bounded (no per-article URL
    explosions).
    """
    from app.metrics import http_request_duration_seconds, http_requests_total
    if request.url.path == "/metrics":
        return await call_next(request)
    start = _time()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        http_request_duration_seconds.labels(method=request.method).observe(_time() - start)
        http_requests_total.labels(
            method=request.method,
            status_class=f"{status // 100}xx",
        ).inc()


class RssCandidateOut(BaseModel):
    title: str
    url: str
    summary: str | None = None
    published_at: str | None = None
    score: float
    source_id: str
    source_name: str


class PrepareArticleReq(BaseModel):
    source_id: str
    article_url: str
    article_title: str = Field(default="", max_length=500)
    article_summary: str | None = Field(default=None, max_length=5000)
    article_published_at: str | None = None
    n_scenes: int = Field(default=DEFAULT_SCENES, ge=0, le=20)
    target_seconds: int = Field(default=DEFAULT_TARGET_SECONDS, ge=30, le=600)
    language: str = DEFAULT_LANGUAGE
    selected_platforms: list[str] = Field(default_factory=lambda: [DEFAULT_PLATFORM])
    animation_prompt: str | None = Field(default=None, max_length=500)


class GenerateReq(BaseModel):
    source_id: str
    voice_id: str | None = None
    target_seconds: int = Field(default=DEFAULT_TARGET_SECONDS, ge=30, le=600)
    n_scenes: int = Field(default=DEFAULT_SCENES, ge=0, le=20)
    article_url: str | None = None
    article_title: str | None = Field(default=None, max_length=500)
    article_summary: str | None = Field(default=None, max_length=5000)
    article_published_at: str | None = None
    article_id: str | None = None


class GenerateVideoReq(BaseModel):
    article_id: str
    audio_asset_id: str | None = None
    burn_subtitles: bool = True
    render_mode: Literal["static", "animated"] = "static"
    platform: str = DEFAULT_PLATFORM
    animation_prompt: str | None = None


# ── RSS candidate picker ───────────────────────────────────────────────────

def _rss_parse_dt(entry) -> datetime | None:
    for k in ("published_parsed", "updated_parsed"):
        t = getattr(entry, k, None)
        if t:
            return datetime(*t[:6])
    return None


def _rss_score_entry(entry, now: datetime) -> float:
    score = 0.0
    dt = _rss_parse_dt(entry)
    if dt:
        age_days = max((now - dt).total_seconds() / 86_400, 0)
        score += max(0.0, 5.0 - age_days)
    title = (entry.get("title") or "").strip()
    if 30 <= len(title) <= 150:
        score += 3
    elif len(title) > 10:
        score += 1
    summary = (entry.get("summary") or entry.get("description") or "").strip()
    if len(summary) > 300:
        score += 2
    elif len(summary) > 80:
        score += 1
    return score


@app.get("/rss/candidates", response_model=list[RssCandidateOut], dependencies=[Depends(check_api_key)])
async def get_rss_candidates(
    source_id: str | None = Query(default=None, description="Filter to a single source; omit for all sources"),
    limit: int = Query(default=10, ge=1, le=30),
    db: Session = Depends(get_db),
):
    """Fetch and score RSS candidates for the article picker — no DB writes, no generation."""
    if source_id:
        src = db.get(Source, source_id)
        if not src:
            raise HTTPException(status_code=404, detail="Source not found")
        sources_to_fetch: list[Source] = [src]
    else:
        sources_to_fetch = list(db.execute(select(Source)).scalars().all())

    now = datetime.utcnow()

    def _fetch_one(src_obj: Source) -> list[dict]:
        try:
            feed = _fp.parse(src_obj.rss_url)
            results = []
            for entry in feed.entries[:20]:
                title = (entry.get("title") or "").strip()
                url = (entry.get("link") or "").strip()
                if not url or not title:
                    continue
                summary = (entry.get("summary") or entry.get("description") or "").strip()
                dt = _rss_parse_dt(entry)
                results.append({
                    "title": title,
                    "url": url,
                    "summary": summary[:500] if summary else None,
                    "published_at": dt.isoformat() if dt else None,
                    "score": round(_rss_score_entry(entry, now), 2),
                    "source_id": src_obj.id,
                    "source_name": src_obj.name,
                })
            results.sort(key=lambda c: -c["score"])
            return results
        except Exception as exc:
            logger.warning("RSS candidates fetch failed for '%s': %s", src_obj.name, exc)
            return []

    loop = asyncio.get_event_loop()
    per_source = await asyncio.gather(
        *[loop.run_in_executor(None, _fetch_one, s) for s in sources_to_fetch],
        return_exceptions=True,
    )

    all_candidates: list[dict] = []
    for result in per_source:
        if isinstance(result, list):
            all_candidates.extend(result)

    all_candidates.sort(key=lambda c: -c["score"])

    if source_id:
        return all_candidates[:limit]

    # Multi-source: cap at 3 per source to keep results diverse
    seen: dict[str, int] = {}
    diverse: list[dict] = []
    for c in all_candidates:
        sid = c["source_id"]
        if seen.get(sid, 0) < 3:
            diverse.append(c)
            seen[sid] = seen.get(sid, 0) + 1
        if len(diverse) >= limit:
            break
    return diverse


# ── Global exception handler ───────────────────────────────────────────────

@app.exception_handler(Exception)
async def _unhandled_exception(request: Request, exc: Exception):
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


# Auth + users/me routes live in app.routers.auth now.


# ── Sources ────────────────────────────────────────────────────────────────

@app.get("/sources", dependencies=[Depends(check_api_key)])
def list_sources(db=Depends(get_db)):
    rows = db.execute(select(Source)).scalars().all()
    return [
        {"id": r.id, "name": r.name, "rss_url": r.rss_url, "language_hint": r.language_hint}
        for r in rows
    ]


@app.get("/platforms", dependencies=[Depends(check_api_key)])
def list_platforms():
    """Return available platform output profiles and language options."""
    return {
        "platforms": [PlatformProfileOut(**p.to_dict()) for p in PROFILES.values()],
        "languages": [{"code": code, "label": label} for code, label in LANGUAGES.items()],
        "defaults": {
            "platform": DEFAULT_PLATFORM,
            "language": DEFAULT_LANGUAGE,
            "animation_prompt": DEFAULT_ANIMATION_PROMPT,
        },
    }


# ── Articles ───────────────────────────────────────────────────────────────

@app.get("/articles", dependencies=[Depends(check_api_key)])
def list_articles(
    source_id: str | None = Query(default=None),
    pinned: bool | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_optional_user),
):
    # Single query with LEFT JOIN subqueries — avoids N+1 per article
    audio_sq = (
        select(AudioAsset.article_id, func.count(AudioAsset.id).label("cnt"))
        .where(AudioAsset.status == "ready", AudioAsset.deleted_at.is_(None))
        .group_by(AudioAsset.article_id)
        .subquery()
    )
    video_sq = (
        select(VideoAsset.article_id, func.count(VideoAsset.id).label("cnt"))
        .where(VideoAsset.status == "ready", VideoAsset.deleted_at.is_(None))
        .group_by(VideoAsset.article_id)
        .subquery()
    )
    q = (
        select(Article, audio_sq.c.cnt.label("audio_count"), video_sq.c.cnt.label("video_count"))
        .outerjoin(audio_sq, Article.id == audio_sq.c.article_id)
        .outerjoin(video_sq, Article.id == video_sq.c.article_id)
        .where(Article.deleted_at.is_(None))
        .order_by(Article.is_pinned.desc(), Article.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    if source_id:
        q = q.where(Article.source_id == source_id)
    if current_user:
        q = q.where(Article.user_id == current_user.id)
    if pinned is not None:
        q = q.where(Article.is_pinned == pinned)

    # Count total matching rows (ignoring limit/offset)
    count_q = select(func.count(Article.id)).where(Article.deleted_at.is_(None))
    if source_id:
        count_q = count_q.where(Article.source_id == source_id)
    if current_user:
        count_q = count_q.where(Article.user_id == current_user.id)
    if pinned is not None:
        count_q = count_q.where(Article.is_pinned == pinned)
    total: int = db.execute(count_q).scalar_one()

    rows = db.execute(q).all()
    items = [
        {
            "id": r.Article.id,
            "title": r.Article.title,
            "url": r.Article.url,
            "source_id": r.Article.source_id,
            "created_at": r.Article.created_at,
            "has_audio": bool(r.audio_count),
            "has_video": bool(r.video_count),
            "is_pinned": bool(r.Article.is_pinned),
            "analysis_sentiment": (r.Article.analysis_json or {}).get("sentiment"),
            "analysis_impact": (r.Article.analysis_json or {}).get("impact_score"),
        }
        for r in rows
    ]
    return {"items": items, "total": total, "has_more": (offset + len(items)) < total}


@app.get("/articles/{article_id}", response_model=ArticleResponse, dependencies=[Depends(check_api_key)])
def get_article(article_id: str, db=Depends(get_db), current_user: Optional[User] = Depends(get_optional_user)):
    article = db.get(Article, article_id)
    if not article or article.deleted_at:
        raise HTTPException(status_code=404, detail="Article not found")
    _assert_article_owner(article, current_user)
    storyboard = _load_storyboard(article)
    return ArticleResponse(
        id=article.id,
        source_id=article.source_id,
        title=article.title,
        url=article.url,
        published_at=article.published_at,
        created_at=article.created_at,
        tts_script=article.tts_script,
        script_language=article.script_language,
        summary_model=article.summary_model,
        storyboard=storyboard,
    )


@app.get("/articles/{article_id}/captions.srt", dependencies=[Depends(check_api_key)])
def get_captions_srt(article_id: str, db=Depends(get_db), current_user: Optional[User] = Depends(get_optional_user)):
    article = db.get(Article, article_id)
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    _assert_article_owner(article, current_user)
    storyboard = _load_storyboard(article)
    if not storyboard:
        raise HTTPException(status_code=404, detail="No compatible storyboard — regenerate the article")
    srt = captions_to_srt(storyboard_to_captions(storyboard))
    return Response(
        content=srt,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{article_id}.srt"'},
    )


@app.get("/articles/{article_id}/captions.vtt", dependencies=[Depends(check_api_key)])
def get_captions_vtt(article_id: str, db=Depends(get_db), current_user: Optional[User] = Depends(get_optional_user)):
    article = db.get(Article, article_id)
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    _assert_article_owner(article, current_user)
    storyboard = _load_storyboard(article)
    if not storyboard:
        raise HTTPException(status_code=404, detail="No compatible storyboard — regenerate the article")
    vtt = captions_to_vtt(storyboard_to_captions(storyboard))
    return Response(
        content=vtt,
        media_type="text/vtt; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{article_id}.vtt"'},
    )


@app.get("/articles/{article_id}/package", response_model=ContentPackage, dependencies=[Depends(check_api_key)])
def get_article_package(article_id: str, db=Depends(get_db), current_user: Optional[User] = Depends(get_optional_user)):
    article = db.get(Article, article_id)
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    _assert_article_owner(article, current_user)

    # All ready audios for this article, grouped by platform.
    audio_rows = db.execute(
        select(AudioAsset)
        .where(
            AudioAsset.article_id == article_id,
            AudioAsset.status == "ready",
            AudioAsset.deleted_at.is_(None),
        )
        .order_by(AudioAsset.created_at.desc())
    ).scalars().all()

    def _audio_to_ref(a: AudioAsset) -> AudioAssetRef:
        return AudioAssetRef(
            id=a.id,
            download_url=f"/audio/{a.id}",
            duration_seconds=a.estimated_seconds,
            format=a.output_format,
            word_count=a.word_count,
            voice_id=a.voice_id,
            model_id=a.model_id,
            platform=a.platform,
        )

    audios_list: list[AudioAssetRef] = [_audio_to_ref(a) for a in audio_rows]

    # Canonical audio = longest-duration platform's audio when present.
    audio_ref: AudioAssetRef | None = None
    if audios_list:
        audio_ref = max(audios_list, key=lambda a: a.duration_seconds or 0)

    # Script list: one per platform from platform_scripts_json; fall back to
    # the legacy single-script case.
    scripts_list: list[ScriptAsset] = []
    platform_scripts = (article.platform_scripts_json or {}) if hasattr(article, "platform_scripts_json") else {}
    for platform_id, entry in (platform_scripts or {}).items():
        text = (entry or {}).get("script") or ""
        if not text:
            continue
        audio_for_platform = next((a for a in audios_list if a.platform == platform_id), None)
        actual = audio_for_platform.duration_seconds if audio_for_platform else None
        scripts_list.append(ScriptAsset(
            text=text,
            language=article.script_language or "es-MX",
            word_count=int(entry.get("word_count") or _count_words(text)),
            estimated_duration_seconds=actual or entry.get("estimated_duration_seconds"),
            model=article.summary_model,
            target_seconds=int(entry.get("target_seconds") or 0) or None,
            platform=platform_id,
        ))

    script_asset: ScriptAsset | None = None
    if scripts_list:
        # Primary script = longest target duration
        script_asset = max(scripts_list, key=lambda s: s.target_seconds or 0)
    elif article.tts_script:
        wc = _count_words(article.tts_script)
        actual_duration = audio_ref.duration_seconds if audio_ref else None
        script_asset = ScriptAsset(
            text=article.tts_script,
            language=article.script_language or "es-MX",
            word_count=wc,
            estimated_duration_seconds=actual_duration or _estimate_seconds(wc, article.script_language),
            model=article.summary_model,
        )

    storyboard = _load_storyboard(article)
    captions = storyboard_to_captions(storyboard) if storyboard else None
    visual_prompts: list[VisualPromptEntry] | None = None
    if storyboard:
        visual_prompts = [
            VisualPromptEntry(
                scene_number=s.scene_number,
                visual_prompt=s.visual_prompt,
                on_screen_text=s.on_screen_text,
                asset_type=s.asset_type,
                start_time_estimate=s.start_time_estimate,
            )
            for s in storyboard.scenes
        ]

    image_rows = db.execute(
        select(ImageAsset)
        .where(ImageAsset.article_id == article_id, ImageAsset.deleted_at.is_(None))
        .order_by(ImageAsset.scene_number)
    ).scalars().all()
    images = [
        ImageAssetRef(id=r.id, scene_number=r.scene_number, visual_prompt=r.visual_prompt, status=r.status)
        for r in image_rows
    ] or None

    def _video_to_ref(r: VideoAsset) -> VideoAssetRef:
        return VideoAssetRef(
            id=r.id,
            download_url=f"/video/{r.id}",
            duration_seconds=r.duration_seconds,
            width=r.width,
            height=r.height,
            has_subtitles=bool(r.has_subtitles),
            status=r.status,
            error=r.error,
            render_mode=r.render_mode or "static",
            platform=r.platform,
        )

    all_video_rows = db.execute(
        select(VideoAsset)
        .where(VideoAsset.article_id == article_id, VideoAsset.deleted_at.is_(None))
        .order_by(VideoAsset.created_at.desc())
    ).scalars().all()
    video_ref: VideoAssetRef | None = _video_to_ref(all_video_rows[0]) if all_video_rows else None
    videos_list: list[VideoAssetRef] | None = (
        [_video_to_ref(r) for r in all_video_rows] if all_video_rows else None
    )

    # Per-scene animated clip status (only populated for animated renders)
    scene_video_rows = db.execute(
        select(SceneVideoAsset)
        .where(
            SceneVideoAsset.article_id == article_id,
            SceneVideoAsset.deleted_at.is_(None),
        )
        .order_by(SceneVideoAsset.scene_number)
    ).scalars().all()
    scene_videos: list[SceneVideoRef] | None = (
        [
            SceneVideoRef(
                id=r.id,
                scene_number=r.scene_number,
                provider=r.provider,
                status=r.status,
                duration_seconds=r.duration_seconds,
                error=r.error,
            )
            for r in scene_video_rows
        ]
        if scene_video_rows else None
    )

    analysis: AnalysisResult | None = None
    if article.analysis_json:
        try:
            analysis = AnalysisResult(**article.analysis_json)
        except Exception:
            pass

    social_captions: list[SocialCaption] | None = None
    if article.social_captions_json:
        try:
            social_captions = [
                SocialCaption(platform=plat, **data)
                for plat, data in article.social_captions_json.items()
                if isinstance(data, dict) and "caption" in data
            ]
        except Exception:
            pass

    # Inline cost summary so the frontend gets everything in one request
    cost_summary: CostSummary | None = None
    try:
        from app.usage import get_article_cost_summary
        raw = get_article_cost_summary(db, article_id)
        if raw["event_count"] > 0:
            cost_summary = CostSummary(
                article_id=raw["article_id"],
                total_estimated_usd=raw["total_estimated_usd"],
                by_provider=raw["by_provider"],
                by_stage=raw["by_stage"],
                total_tokens=raw["total_tokens"],
                total_characters=raw["total_characters"],
                event_count=raw["event_count"],
                events=[UsageEventOut(**e) for e in raw["events"]],
                pricing_note=raw["pricing_note"],
            )
    except Exception:
        pass

    source_obj = db.get(Source, article.source_id)

    return ContentPackage(
        article_id=article.id,
        title=article.title,
        url=article.url,
        source_id=article.source_id,
        source_name=source_obj.name if source_obj else None,
        published_at=article.published_at,
        generated_at=article.created_at,
        language=article.language or "es-MX",
        selected_platforms=article.selected_platforms,
        animation_prompt=article.animation_prompt,
        thumbnail_url=f"/thumbnail/{article.id}" if article.thumbnail_path else None,
        script=script_asset,
        scripts=scripts_list or None,
        audio=audio_ref,
        audios=audios_list or None,
        storyboard=storyboard,
        captions=captions,
        visual_prompts=visual_prompts,
        images=images,
        video=video_ref,
        videos=videos_list,
        scene_videos=scene_videos,
        analysis=analysis,
        social_captions=social_captions,
        cost_summary=cost_summary,
    )


@app.get("/articles/{article_id}/costs", response_model=CostSummary, dependencies=[Depends(check_api_key)])
def get_article_costs(article_id: str, db=Depends(get_db), current_user: Optional[User] = Depends(get_optional_user)):
    """Return the full cost and usage breakdown for an article."""
    article = db.get(Article, article_id)
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    _assert_article_owner(article, current_user)
    from app.usage import get_article_cost_summary
    raw = get_article_cost_summary(db, article_id)
    return CostSummary(
        article_id=raw["article_id"],
        total_estimated_usd=raw["total_estimated_usd"],
        by_provider=raw["by_provider"],
        by_stage=raw["by_stage"],
        total_tokens=raw["total_tokens"],
        total_characters=raw["total_characters"],
        event_count=raw["event_count"],
        events=[UsageEventOut(**e) for e in raw["events"]],
        pricing_note=raw["pricing_note"],
    )


@app.get("/articles/{article_id}/images", dependencies=[Depends(check_api_key)])
def list_article_images(article_id: str, db=Depends(get_db), current_user: Optional[User] = Depends(get_optional_user)):
    article = db.get(Article, article_id)
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    _assert_article_owner(article, current_user)
    rows = db.execute(
        select(ImageAsset)
        .where(ImageAsset.article_id == article_id, ImageAsset.deleted_at.is_(None))
        .order_by(ImageAsset.scene_number)
    ).scalars().all()
    return [
        {"id": r.id, "scene_number": r.scene_number, "visual_prompt": r.visual_prompt,
         "status": r.status, "created_at": r.created_at}
        for r in rows
    ]


# GET /thumbnail/{article_id} lives in app.routers.media.


class ThumbnailReq(BaseModel):
    prompt: str | None = Field(default=None, max_length=900)


@app.post("/articles/{article_id}/thumbnail", dependencies=[Depends(check_api_key)])
def generate_thumbnail_endpoint(
    article_id: str,
    req: ThumbnailReq,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_optional_user),
):
    """Queue a thumbnail generation job; accepts an optional custom prompt."""
    article = db.get(Article, article_id)
    if not article or article.deleted_at:
        raise HTTPException(status_code=404, detail="Article not found")
    _assert_article_owner(article, current_user)

    uid = current_user.id if current_user else "anon"
    _rate_limit(f"rl:thumbnail:{uid}", limit=10, window=60)
    if current_user:
        _check_user_task_capacity(current_user.id)

    openai_key, _, _ = _resolve_user_keys(current_user, db)
    task = celery_app.send_task(
        "generate_article_thumbnail",
        kwargs={
            "article_id": article.id,
            "prompt": (req.prompt or "").strip() or None,
            "openai_api_key": openai_key,
            "user_id": current_user.id if current_user else None,
        },
    )
    _store_task_owner(task.id, current_user.id if current_user else None)
    if current_user:
        _register_user_task(current_user.id, task.id)
    return {"task_id": task.id, "status": "queued"}


@app.post("/articles/{article_id}/thumbnail/upload", dependencies=[Depends(check_api_key)])
async def upload_thumbnail(
    article_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_optional_user),
):
    """Replace the article's thumbnail with a user-uploaded image."""
    article = db.get(Article, article_id)
    if not article or article.deleted_at:
        raise HTTPException(status_code=404, detail="Article not found")
    _assert_article_owner(article, current_user)

    content_type = (file.content_type or "").lower()
    allowed = {"image/png": "png", "image/jpeg": "jpg", "image/jpg": "jpg", "image/webp": "webp"}
    if content_type not in allowed:
        raise HTTPException(status_code=415, detail=f"Unsupported type: {content_type}; use PNG/JPEG/WEBP")

    data = await file.read()
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File too large (max 10 MB)")
    if len(data) < 100:
        raise HTTPException(status_code=422, detail="File is empty or too small")

    image_dir = os.getenv("IMAGE_DIR", "/data/images")
    os.makedirs(image_dir, exist_ok=True)
    ext = allowed[content_type]
    thumb_path = os.path.join(image_dir, f"{article.id}_thumbnail.{ext}")

    # Remove any previously generated thumbnail with a different extension
    if article.thumbnail_path and article.thumbnail_path != thumb_path and os.path.exists(article.thumbnail_path):
        try:
            os.remove(article.thumbnail_path)
        except OSError:
            pass

    with open(thumb_path, "wb") as fh:
        fh.write(data)
    article.thumbnail_path = thumb_path
    db.commit()
    return {"article_id": article.id, "thumbnail_url": f"/thumbnail/{article.id}"}


class PinReq(BaseModel):
    pinned: bool = True


@app.patch("/articles/{article_id}/pin", dependencies=[Depends(check_api_key)])
def toggle_pin(article_id: str, req: PinReq, db: Session = Depends(get_db), current_user: Optional[User] = Depends(get_optional_user)):
    """Pin or unpin an article for quick access."""
    article = db.get(Article, article_id)
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    _assert_article_owner(article, current_user)
    article.is_pinned = 1 if req.pinned else 0
    db.commit()
    return {"article_id": article_id, "is_pinned": bool(article.is_pinned)}


class StoryboardReorderReq(BaseModel):
    scene_order: list[int]  # original scene_numbers in the desired new sequence


@app.patch("/articles/{article_id}/storyboard", dependencies=[Depends(check_api_key)])
def reorder_storyboard(
    article_id: str,
    req: StoryboardReorderReq,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_optional_user),
):
    """Reorder storyboard scenes. scene_order is the list of original scene_numbers in new sequence."""
    article = db.get(Article, article_id)
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    _assert_article_owner(article, current_user)
    storyboard = _load_storyboard(article)
    if not storyboard:
        raise HTTPException(status_code=400, detail="Article has no storyboard")

    existing_nums = {s.scene_number for s in storyboard.scenes}
    if set(req.scene_order) != existing_nums or len(req.scene_order) != len(existing_nums):
        raise HTTPException(status_code=422, detail="scene_order must be a permutation of existing scene numbers")

    scene_map = {s.scene_number: s for s in storyboard.scenes}
    reordered = []
    start = 0.0
    for original_num in req.scene_order:
        s = scene_map[original_num]
        data = s.model_dump()
        data["start_time_estimate"] = round(start, 1)
        reordered.append(data)
        start += s.duration_estimate

    article.storyboard_json = {
        "scenes": reordered,
        "total_duration_estimate": storyboard.total_duration_estimate,
    }
    db.commit()
    return {"article_id": article_id, "scene_count": len(reordered)}


_ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
_IMAGE_EXT_MAP = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif"}


@app.post("/articles/{article_id}/scenes/{scene_number}/image", dependencies=[Depends(check_api_key)])
async def upload_scene_image(
    article_id: str,
    scene_number: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_optional_user),
):
    """Replace a scene's image with a user-uploaded file (PNG/JPEG/WEBP, max 20 MB)."""
    article = db.get(Article, article_id)
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    _assert_article_owner(article, current_user)

    # Validate scene_number is in the actual storyboard
    storyboard = _load_storyboard(article)
    if storyboard and scene_number not in {s.scene_number for s in storyboard.scenes}:
        raise HTTPException(status_code=422, detail="scene_number not in article storyboard")

    content_type = (file.content_type or "").lower()
    if content_type not in _ALLOWED_IMAGE_TYPES:
        raise HTTPException(status_code=422, detail="Only PNG, JPEG, WEBP, or GIF images are accepted")

    contents = await file.read()
    if len(contents) > 20 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File too large — maximum 20 MB")

    # Magic-byte validation (first 8 bytes)
    _MAGIC: dict[bytes, str] = {
        b"\x89PNG\r\n\x1a\n": "image/png",
        b"\xff\xd8\xff": "image/jpeg",
        b"RIFF": "image/webp",
        b"GIF8": "image/gif",
    }
    magic_ok = any(contents[:len(sig)] == sig for sig in _MAGIC)
    if not magic_ok:
        raise HTTPException(status_code=422, detail="File content does not match a supported image format")

    image_dir = os.getenv("IMAGE_DIR", "/data/images")
    os.makedirs(image_dir, exist_ok=True)
    ext = _IMAGE_EXT_MAP.get(content_type, "png")
    save_path = os.path.join(image_dir, f"{article_id}_scene_{scene_number}_upload.{ext}")

    # Atomic write: temp file → rename to prevent partial reads
    import tempfile as _tmpmod
    with _tmpmod.NamedTemporaryFile(delete=False, dir=image_dir, suffix=f".{ext}") as tmp:
        tmp.write(contents)
        tmp_path = tmp.name
    os.replace(tmp_path, save_path)

    existing = db.execute(
        select(ImageAsset).where(
            ImageAsset.article_id == article_id,
            ImageAsset.scene_number == scene_number,
            ImageAsset.deleted_at.is_(None),
        )
    ).scalar_one_or_none()

    if existing:
        if existing.file_path != save_path and os.path.exists(existing.file_path):
            try:
                os.remove(existing.file_path)
            except OSError:
                pass
        existing.file_path = save_path
        existing.provider = "upload"
        existing.model = "user"
        existing.status = "ready"
        existing.error = None
        img = existing
    else:
        img = ImageAsset(
            article_id=article_id,
            scene_number=scene_number,
            visual_prompt="[User upload]",
            file_path=save_path,
            provider="upload",
            model="user",
            status="ready",
        )
        db.add(img)

    db.commit()
    return {"id": img.id, "scene_number": scene_number, "status": "ready", "url": f"/image/{img.id}"}


class ScriptEditReq(BaseModel):
    text: str = Field(..., min_length=1, max_length=20000)


@app.patch("/articles/{article_id}/script", dependencies=[Depends(check_api_key)])
def update_article_script(
    article_id: str,
    req: ScriptEditReq,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_optional_user),
):
    """Replace the article's TTS script. Existing audio/video assets are preserved."""
    article = db.get(Article, article_id)
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    _assert_article_owner(article, current_user)
    text = req.text.strip()
    if not text:
        raise HTTPException(status_code=422, detail="Script text cannot be empty")
    article.tts_script = text
    db.commit()
    return {"article_id": article_id, "word_count": len(text.split())}


class RegenerateScriptReq(BaseModel):
    n_scenes: int = Field(default=8, ge=0, le=20)


@app.post("/articles/{article_id}/regenerate-script", dependencies=[Depends(check_api_key)])
def trigger_regenerate_script(
    article_id: str,
    req: RegenerateScriptReq,
    request: Request,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_optional_user),
):
    """Re-run script + storyboard generation from the article's existing raw_text."""
    article = db.get(Article, article_id)
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    _assert_article_owner(article, current_user)
    if not article.raw_text:
        raise HTTPException(status_code=400, detail="Article has no raw text — cannot regenerate script")

    uid = current_user.id if current_user else f"ip:{(request.client.host if request.client else 'x')}"
    _rate_limit(f"rl:regen-script:{uid}", limit=10, window=60)
    if current_user:
        _check_user_task_capacity(current_user.id)

    openai_key, _, _ = _resolve_user_keys(current_user, db)
    task = celery_app.send_task(
        "regenerate_script_for_article",
        kwargs={
            "article_id": article_id,
            "n_scenes": req.n_scenes,
            "openai_api_key": openai_key,
        },
    )
    _store_task_owner(task.id, current_user.id if current_user else None)
    if current_user:
        _register_user_task(current_user.id, task.id)
    return {"task_id": task.id, "status": "queued"}


@app.get("/articles/{article_id}/export.zip", dependencies=[Depends(check_api_key)])
def export_article_zip(article_id: str, db: Session = Depends(get_db), current_user: Optional[User] = Depends(get_optional_user)):
    """Download a ZIP bundle: script, audio, video, captions, and scene images."""
    article = db.get(Article, article_id)
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    _assert_article_owner(article, current_user)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        if article.tts_script:
            zf.writestr("script.txt", article.tts_script)

        audio = _latest_audio(article_id, db)
        if audio and os.path.exists(audio.file_path):
            zf.write(audio.file_path, "audio.mp3")

        storyboard = _load_storyboard(article)
        if storyboard:
            caps = storyboard_to_captions(storyboard)
            zf.writestr("captions.srt", captions_to_srt(caps))
            zf.writestr("captions.vtt", captions_to_vtt(caps))

        video_row = db.execute(
            select(VideoAsset)
            .where(
                VideoAsset.article_id == article_id,
                VideoAsset.status == "ready",
                VideoAsset.deleted_at.is_(None),
            )
            .order_by(VideoAsset.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        if video_row and os.path.exists(video_row.file_path):
            zf.write(video_row.file_path, "video.mp4")

        image_rows = db.execute(
            select(ImageAsset)
            .where(
                ImageAsset.article_id == article_id,
                ImageAsset.status == "ready",
                ImageAsset.deleted_at.is_(None),
            )
            .order_by(ImageAsset.scene_number)
        ).scalars().all()
        for img in image_rows:
            if os.path.exists(img.file_path):
                zf.write(img.file_path, f"images/scene_{img.scene_number:02d}.png")

    buf.seek(0)
    safe_title = "".join(
        c if c.isalnum() or c in "-_" else "_"
        for c in (article.title or article_id)[:40]
    )
    return Response(
        content=buf.read(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{safe_title}.zip"'},
    )


# ── Jobs ───────────────────────────────────────────────────────────────────

@app.post("/articles/prepare", dependencies=[Depends(check_api_key)])
def prepare_article_endpoint(
    req: PrepareArticleReq,
    request: Request,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_optional_user),
):
    """Queue script + storyboard generation without TTS. Returns a task_id to poll."""
    # SSRF guard
    from app.extract import _validate_article_url
    try:
        _validate_article_url(req.article_url)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    uid = current_user.id if current_user else f"ip:{(request.client.host if request.client else 'x')}"
    _rate_limit(f"rl:prepare:{uid}", limit=10, window=60)
    if current_user:
        _check_user_task_capacity(current_user.id)

    if not db.get(Source, req.source_id):
        raise HTTPException(status_code=404, detail="Unknown source_id")
    openai_key, _, el_voice = _resolve_user_keys(current_user, db)
    # Model + speed drive the VoiceCalibration lookup inside the task.
    el_model = os.getenv("ELEVENLABS_MODEL_ID", "eleven_multilingual_v2")
    el_speed = float(os.getenv("ELEVENLABS_SPEED", "1.0"))
    task = celery_app.send_task(  # type: ignore[assignment]
        "prepare_article",
        kwargs={
            "source_id": req.source_id,
            "article_url": req.article_url,
            "article_title": req.article_title,
            "article_summary": req.article_summary,
            "article_published_at": req.article_published_at,
            "n_scenes": req.n_scenes,
            "target_seconds": req.target_seconds,
            "openai_api_key": openai_key,
            "user_id": current_user.id if current_user else None,
            "language": req.language,
            "selected_platforms": req.selected_platforms,
            "animation_prompt": req.animation_prompt,
            "voice_id": el_voice,
            "voice_model_id": el_model,
            "voice_speed": el_speed,
        },
    )
    _store_task_owner(task.id, current_user.id if current_user else None)
    if current_user:
        _register_user_task(current_user.id, task.id)
    return {"task_id": task.id, "status": "queued"}


@app.post("/generate", dependencies=[Depends(check_api_key)])
def generate(
    req: GenerateReq,
    request: Request,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_optional_user),
):
    src = db.get(Source, req.source_id)
    if not src:
        raise HTTPException(status_code=404, detail="Unknown source_id")

    uid = current_user.id if current_user else f"ip:{(request.client.host if request.client else 'x')}"
    _rate_limit(f"rl:generate:{uid}", limit=5, window=60)
    if current_user:
        _check_user_task_capacity(current_user.id)

    # SSRF guard on optional pre-selected URL
    if req.article_url:
        from app.extract import _validate_article_url
        try:
            _validate_article_url(req.article_url)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    # Return existing in-flight task rather than spawning a duplicate
    task_key = f"{req.source_id}:{current_user.id if current_user else 'anon'}"
    existing = _audio_tasks.get(task_key)
    if existing:
        res = AsyncResult(existing, app=celery_app)
        if res.state in ("PENDING", "RECEIVED", "STARTED", "PROGRESS"):
            return {"task_id": existing, "status": "already_running"}

    openai_key, el_key, el_voice = _resolve_user_keys(current_user, db)

    task = celery_app.send_task(
        "generate_latest_for_source",
        kwargs={
            "source_id": req.source_id,
            "voice_id": el_voice or req.voice_id,
            "target_seconds": req.target_seconds,
            "n_scenes": req.n_scenes,
            "openai_api_key": openai_key,
            "elevenlabs_api_key": el_key,
            "user_id": current_user.id if current_user else None,
            "article_url": req.article_url,
            "article_title": req.article_title,
            "article_summary": req.article_summary,
            "article_published_at": req.article_published_at,
            "article_id": req.article_id,
        },
    )
    _audio_tasks[task_key] = task.id
    _store_task_owner(task.id, current_user.id if current_user else None)
    if current_user:
        _register_user_task(current_user.id, task.id)
    return {"task_id": task.id, "status": "queued"}


@app.get("/jobs/{task_id}", dependencies=[Depends(check_api_key)])
def job_status(task_id: str, current_user: Optional[User] = Depends(get_optional_user)):
    _assert_task_owner(task_id, current_user)
    res = AsyncResult(task_id, app=celery_app)
    payload: dict = {"task_id": task_id, "state": res.state}

    if res.successful():
        payload["result"] = res.result
        audio_id = (res.result or {}).get("audio_id")
        if audio_id:
            payload["result"]["audio_url"] = f"/audio/{audio_id}"
    elif res.failed():
        payload["error"] = str(res.result)
    elif res.state == "PROGRESS":
        payload["meta"] = res.info or {}

    return payload


@app.delete("/jobs/{task_id}", dependencies=[Depends(check_api_key)])
def cancel_job(task_id: str, current_user: Optional[User] = Depends(get_optional_user)):
    """Revoke a queued or running task. Sends SIGTERM to the worker process."""
    _assert_task_owner(task_id, current_user)
    celery_app.control.revoke(task_id, terminate=True, signal="SIGTERM")
    return {"cancelled": task_id}


@app.post("/generate-video", dependencies=[Depends(check_api_key)])
def generate_video(
    req: GenerateVideoReq,
    request: Request,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_optional_user),
):
    article = db.get(Article, req.article_id)
    if not article:
        raise HTTPException(status_code=404, detail="Unknown article_id")
    _assert_article_owner(article, current_user)
    if not article.storyboard_json:
        raise HTTPException(status_code=400, detail="Article has no storyboard — generate audio first")

    uid = current_user.id if current_user else f"ip:{(request.client.host if request.client else 'x')}"
    _rate_limit(f"rl:video:{uid}", limit=5, window=60)
    if current_user:
        _check_user_task_capacity(current_user.id)

    existing = _video_tasks.get(req.article_id)
    if existing:
        res = AsyncResult(existing, app=celery_app)
        if res.state in ("PENDING", "RECEIVED", "STARTED", "PROGRESS"):
            return {"task_id": existing, "status": "already_running"}

    openai_key, _, _ = _resolve_user_keys(current_user, db)
    task_name = (
        "generate_animated_video_for_article"
        if req.render_mode == "animated"
        else "generate_video_for_article"
    )
    task = celery_app.send_task(
        task_name,
        kwargs={
            "article_id":    req.article_id,
            "audio_asset_id": req.audio_asset_id,
            "burn_subtitles": req.burn_subtitles,
            "openai_api_key": openai_key,
            "platform": req.platform,
            "animation_prompt": req.animation_prompt,
        },
    )
    _video_tasks[req.article_id] = task.id
    _store_task_owner(task.id, current_user.id if current_user else None)
    if current_user:
        _register_user_task(current_user.id, task.id)
    return {"task_id": task.id, "status": "queued", "render_mode": req.render_mode}


# Static-asset GETs (/audio, /image, /video, /scene-videos, /thumbnail)
# live in app.routers.media.


class RegenerateReq(BaseModel):
    stage: Literal["video", "images"]
    burn_subtitles: bool = True


@app.post("/articles/{article_id}/regenerate", dependencies=[Depends(check_api_key)])
def regenerate_stage(
    article_id: str,
    req: RegenerateReq,
    request: Request,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_optional_user),
):
    """Re-run a specific pipeline stage for an existing article.

    stage=video  — re-queue video assembly, reusing any already-generated images.
    stage=images — delete all image records first so every scene gets a fresh
                   DALL-E call, then assemble the video.
    """
    article = db.get(Article, article_id)
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    _assert_article_owner(article, current_user)
    if not article.storyboard_json:
        raise HTTPException(status_code=400, detail="Article has no storyboard — generate audio first")

    uid = current_user.id if current_user else f"ip:{(request.client.host if request.client else 'x')}"
    _rate_limit(f"rl:regen:{uid}", limit=5, window=60)
    if current_user:
        _check_user_task_capacity(current_user.id)

    if req.stage == "images":
        # Soft-delete existing image assets instead of hard-delete so audit
        # trail + reprocess history survives across regenerations.
        now = datetime.utcnow()
        db.execute(
            ImageAsset.__table__.update()
            .where(
                ImageAsset.article_id == article_id,
                ImageAsset.deleted_at.is_(None),
            )
            .values(deleted_at=now)
        )
        db.commit()

    existing = _video_tasks.get(article_id)
    if existing:
        res = AsyncResult(existing, app=celery_app)
        if res.state in ("PENDING", "RECEIVED", "STARTED", "PROGRESS"):
            return {"task_id": existing, "status": "already_running"}

    openai_key, _, _ = _resolve_user_keys(current_user, db)
    task = celery_app.send_task(
        "generate_video_for_article",
        kwargs={
            "article_id": article_id,
            "burn_subtitles": req.burn_subtitles,
            "openai_api_key": openai_key,
        },
    )
    _video_tasks[article_id] = task.id
    _store_task_owner(task.id, current_user.id if current_user else None)
    if current_user:
        _register_user_task(current_user.id, task.id)
    return {"task_id": task.id, "status": "queued", "stage": req.stage}


# ── Article soft-delete ────────────────────────────────────────────────────

@app.delete("/articles/{article_id}", dependencies=[Depends(check_api_key)])
def delete_article(
    article_id: str,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_optional_user),
):
    """Soft-delete an article. Assets remain on disk; the row is hidden from all list/get queries."""
    article = db.get(Article, article_id)
    if not article or article.deleted_at:
        raise HTTPException(status_code=404, detail="Article not found")
    _assert_article_owner(article, current_user)
    article.deleted_at = datetime.utcnow()
    db.commit()
    return {"deleted": article_id}


# /admin/beat-info, /health, /metrics live in app.routers.health.


# ── Helpers ────────────────────────────────────────────────────────────────
# _resolve_user_keys is imported from app.deps.resolve_user_keys above.


def _load_storyboard(article: Article) -> Storyboard | None:
    if not article.storyboard_json:
        return None
    try:
        return Storyboard(**article.storyboard_json)
    except Exception:
        return None


def _latest_audio(article_id: str, db) -> AudioAsset | None:
    return db.execute(
        select(AudioAsset)
        .where(AudioAsset.article_id == article_id, AudioAsset.deleted_at.is_(None))
        .order_by(AudioAsset.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
