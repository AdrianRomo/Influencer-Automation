import asyncio
import io
import json
import logging
import os
import zipfile
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime
from time import time as _time
import feedparser as _fp
from fastapi import FastAPI, Depends, File, HTTPException, Header, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from celery.result import AsyncResult
from redis import Redis as RedisClient
from sqlalchemy import select, func, delete
from sqlalchemy.orm import Session
from typing import Literal, Optional

logger = logging.getLogger(__name__)

# ── Auth rate limiting (in-memory, single-instance) ────────────────────────
# For multi-worker deployments, replace with Redis-backed rate limiting.
_auth_attempts: dict[str, list[float]] = defaultdict(list)
_AUTH_WINDOW = 60   # seconds
_AUTH_MAX = 10      # max attempts per window per IP


def _check_auth_rate(request: Request) -> None:
    ip = (request.client.host if request.client else None) or "unknown"
    now = _time()
    recent = [t for t in _auth_attempts[ip] if now - t < _AUTH_WINDOW]
    if len(recent) >= _AUTH_MAX:
        raise HTTPException(
            status_code=429,
            detail=f"Too many authentication attempts — please wait {_AUTH_WINDOW}s",
        )
    recent.append(now)
    _auth_attempts[ip] = recent

from app.auth import (
    create_access_token, decode_access_token,
    hash_password, verify_password,
    encrypt_api_key, decrypt_api_key,
)
from app.db import get_db, engine
from app.models import Base, Source, AudioAsset, Article, ImageAsset, VideoAsset, User, UserApiKeys
from app.rss_sources import SOURCES
from app.schemas import (
    ArticleResponse, Storyboard,
    AudioAssetRef, ScriptAsset, VisualPromptEntry, ContentPackage,
    ImageAssetRef, VideoAssetRef, AnalysisResult,
    RegisterReq, LoginReq, TokenResp, UserResp, UserKeysIn, UserKeysOut,
)
from app.captions import storyboard_to_captions, captions_to_srt, captions_to_vtt
from app.summarize import _count_words, _estimate_seconds
from app.tasks import celery_app

Base.metadata.create_all(bind=engine)

DEFAULT_TARGET_SECONDS = int(os.getenv("TTS_TARGET_SECONDS", "180"))
DEFAULT_SCENES = int(os.getenv("STORYBOARD_SCENES", "8"))
API_KEY = os.getenv("API_KEY", "").strip()

# In-memory idempotency: one active task per source/article
_audio_tasks: dict[str, str] = {}
_video_tasks: dict[str, str] = {}


# ── Auth dependencies ──────────────────────────────────────────────────────

def _user_from_bearer(token: str, db: Session) -> Optional[User]:
    user_id = decode_access_token(token)
    if not user_id:
        return None
    return db.get(User, user_id)


def get_optional_user(
    authorization: str = Header(default=""),
    db: Session = Depends(get_db),
) -> Optional[User]:
    """Return the authenticated User or None — never raises."""
    if authorization.startswith("Bearer "):
        return _user_from_bearer(authorization[7:], db)
    return None


def check_api_key(
    x_api_key: str = Header(default=""),
    authorization: str = Header(default=""),
):
    """Accept either a valid Bearer JWT (signature only) or the server-level X-API-Key.

    Avoids a DB lookup here — the user record is fetched only when needed via
    get_optional_user(), which runs as a separate dependency on endpoints that
    require user context.
    """
    if authorization.startswith("Bearer "):
        if decode_access_token(authorization[7:]) is not None:
            return  # valid JWT signature
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing auth")


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
    _cors_origins.append(f"https://autonarrator.{_domain}")
    _cors_origins.append(f"http://autonarrator.{_domain}")

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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
    article_title: str = ""
    article_summary: str | None = None
    article_published_at: str | None = None
    n_scenes: int = Field(default=DEFAULT_SCENES, ge=0, le=20)
    target_seconds: int = Field(default=DEFAULT_TARGET_SECONDS, ge=30, le=600)


class GenerateReq(BaseModel):
    source_id: str
    voice_id: str | None = None
    target_seconds: int = Field(default=DEFAULT_TARGET_SECONDS, ge=30, le=600)
    n_scenes: int = Field(default=DEFAULT_SCENES, ge=0, le=20)
    # Pre-selected article from the RSS picker — when set, the task skips RSS auto-pick
    article_url: str | None = None
    article_title: str | None = None
    article_summary: str | None = None
    article_published_at: str | None = None
    # Pre-prepared article with script — when set, the task skips straight to TTS
    article_id: str | None = None


class GenerateVideoReq(BaseModel):
    article_id: str
    audio_asset_id: str | None = None
    burn_subtitles: bool = True


# ── Health ─────────────────────────────────────────────────────────────────

@app.get("/health")
def health(db: Session = Depends(get_db)):
    """Returns service health including DB and Redis reachability."""
    status = "ok"
    db_status = "ok"
    redis_status = "ok"

    try:
        db.execute(func.now())
    except Exception as exc:
        logger.error("Health check DB error: %s", exc)
        db_status = "error"
        status = "degraded"

    try:
        redis_url = os.getenv("CELERY_BROKER_URL", "redis://redis:6379/0")
        rc = RedisClient.from_url(redis_url, socket_connect_timeout=1)
        rc.ping()
    except Exception as exc:
        logger.error("Health check Redis error: %s", exc)
        redis_status = "error"
        status = "degraded"

    return JSONResponse(
        status_code=200 if status == "ok" else 503,
        content={"status": status, "db": db_status, "redis": redis_status},
    )


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


# ── Auth ───────────────────────────────────────────────────────────────────

@app.post("/auth/register", response_model=TokenResp)
def register(req: RegisterReq, request: Request, db: Session = Depends(get_db)):
    _check_auth_rate(request)
    email = req.email.lower().strip()
    if db.execute(select(User).where(User.email == email)).scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Email already registered")
    if len(req.password) < 8:
        raise HTTPException(status_code=422, detail="Password must be at least 8 characters")
    user = User(email=email, hashed_password=hash_password(req.password))
    db.add(user)
    db.commit()
    return TokenResp(
        access_token=create_access_token(user.id),
        user_id=user.id,
        email=user.email,
    )


@app.post("/auth/login", response_model=TokenResp)
def login(req: LoginReq, request: Request, db: Session = Depends(get_db)):
    _check_auth_rate(request)
    email = req.email.lower().strip()
    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if not user or not verify_password(req.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    return TokenResp(
        access_token=create_access_token(user.id),
        user_id=user.id,
        email=user.email,
    )


@app.get("/users/me", response_model=UserResp, dependencies=[Depends(check_api_key)])
def get_me(current_user: Optional[User] = Depends(get_optional_user)):
    if not current_user:
        raise HTTPException(status_code=401, detail="JWT required for this endpoint")
    return UserResp(
        id=current_user.id,
        email=current_user.email,
        created_at=current_user.created_at,
        has_keys=current_user.api_keys is not None,
    )


@app.put("/users/me/keys", response_model=UserKeysOut, dependencies=[Depends(check_api_key)])
def upsert_user_keys(
    req: UserKeysIn,
    current_user: Optional[User] = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    if not current_user:
        raise HTTPException(status_code=401, detail="JWT required for this endpoint")
    keys = db.get(UserApiKeys, current_user.id)
    if not keys:
        keys = UserApiKeys(user_id=current_user.id, updated_at=datetime.utcnow())
        db.add(keys)
    if req.openai_key is not None:
        keys.openai_key_enc = encrypt_api_key(req.openai_key) if req.openai_key else None
    if req.elevenlabs_key is not None:
        keys.elevenlabs_key_enc = encrypt_api_key(req.elevenlabs_key) if req.elevenlabs_key else None
    if req.elevenlabs_voice_id is not None:
        keys.elevenlabs_voice_id = req.elevenlabs_voice_id or None
    if req.elevenlabs_model_id is not None:
        keys.elevenlabs_model_id = req.elevenlabs_model_id or None
    keys.updated_at = datetime.utcnow()
    db.commit()
    return UserKeysOut(
        has_openai_key=bool(keys.openai_key_enc),
        has_elevenlabs_key=bool(keys.elevenlabs_key_enc),
        elevenlabs_voice_id=keys.elevenlabs_voice_id,
        elevenlabs_model_id=keys.elevenlabs_model_id,
    )


@app.get("/users/me/keys", response_model=UserKeysOut, dependencies=[Depends(check_api_key)])
def get_user_keys(
    current_user: Optional[User] = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    if not current_user:
        raise HTTPException(status_code=401, detail="JWT required for this endpoint")
    keys = db.get(UserApiKeys, current_user.id)
    if not keys:
        return UserKeysOut(has_openai_key=False, has_elevenlabs_key=False)
    return UserKeysOut(
        has_openai_key=bool(keys.openai_key_enc),
        has_elevenlabs_key=bool(keys.elevenlabs_key_enc),
        elevenlabs_voice_id=keys.elevenlabs_voice_id,
        elevenlabs_model_id=keys.elevenlabs_model_id,
    )


# ── Sources ────────────────────────────────────────────────────────────────

@app.get("/sources", dependencies=[Depends(check_api_key)])
def list_sources(db=Depends(get_db)):
    rows = db.execute(select(Source)).scalars().all()
    return [
        {"id": r.id, "name": r.name, "rss_url": r.rss_url, "language_hint": r.language_hint}
        for r in rows
    ]


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
        .where(AudioAsset.status == "ready")
        .group_by(AudioAsset.article_id)
        .subquery()
    )
    video_sq = (
        select(VideoAsset.article_id, func.count(VideoAsset.id).label("cnt"))
        .where(VideoAsset.status == "ready")
        .group_by(VideoAsset.article_id)
        .subquery()
    )
    q = (
        select(Article, audio_sq.c.cnt.label("audio_count"), video_sq.c.cnt.label("video_count"))
        .outerjoin(audio_sq, Article.id == audio_sq.c.article_id)
        .outerjoin(video_sq, Article.id == video_sq.c.article_id)
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
    count_q = select(func.count(Article.id))
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
def get_article(article_id: str, db=Depends(get_db)):
    article = db.get(Article, article_id)
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
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
def get_captions_srt(article_id: str, db=Depends(get_db)):
    article = db.get(Article, article_id)
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
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
def get_captions_vtt(article_id: str, db=Depends(get_db)):
    article = db.get(Article, article_id)
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
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
def get_article_package(article_id: str, db=Depends(get_db)):
    article = db.get(Article, article_id)
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")

    audio_ref: AudioAssetRef | None = None
    audio_row = _latest_audio(article_id, db)
    if audio_row:
        audio_ref = AudioAssetRef(
            id=audio_row.id,
            download_url=f"/audio/{audio_row.id}",
            duration_seconds=audio_row.estimated_seconds,
            format=audio_row.output_format,
            word_count=audio_row.word_count,
            voice_id=audio_row.voice_id,
            model_id=audio_row.model_id,
        )

    script_asset: ScriptAsset | None = None
    if article.tts_script:
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
        .where(ImageAsset.article_id == article_id)
        .order_by(ImageAsset.scene_number)
    ).scalars().all()
    images = [
        ImageAssetRef(id=r.id, scene_number=r.scene_number, visual_prompt=r.visual_prompt, status=r.status)
        for r in image_rows
    ] or None

    video_ref: VideoAssetRef | None = None
    video_row = db.execute(
        select(VideoAsset)
        .where(VideoAsset.article_id == article_id)
        .order_by(VideoAsset.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if video_row:
        video_ref = VideoAssetRef(
            id=video_row.id,
            download_url=f"/video/{video_row.id}",
            duration_seconds=video_row.duration_seconds,
            width=video_row.width,
            height=video_row.height,
            has_subtitles=bool(video_row.has_subtitles),
            status=video_row.status,
            error=video_row.error,
        )

    analysis: AnalysisResult | None = None
    if article.analysis_json:
        try:
            analysis = AnalysisResult(**article.analysis_json)
        except Exception:
            pass

    return ContentPackage(
        article_id=article.id,
        title=article.title,
        url=article.url,
        source_id=article.source_id,
        generated_at=article.created_at,
        script=script_asset,
        audio=audio_ref,
        storyboard=storyboard,
        captions=captions,
        visual_prompts=visual_prompts,
        images=images,
        video=video_ref,
        analysis=analysis,
    )


@app.get("/articles/{article_id}/images", dependencies=[Depends(check_api_key)])
def list_article_images(article_id: str, db=Depends(get_db)):
    if not db.get(Article, article_id):
        raise HTTPException(status_code=404, detail="Article not found")
    rows = db.execute(
        select(ImageAsset)
        .where(ImageAsset.article_id == article_id)
        .order_by(ImageAsset.scene_number)
    ).scalars().all()
    return [
        {"id": r.id, "scene_number": r.scene_number, "visual_prompt": r.visual_prompt,
         "status": r.status, "created_at": r.created_at}
        for r in rows
    ]


class PinReq(BaseModel):
    pinned: bool = True


@app.patch("/articles/{article_id}/pin", dependencies=[Depends(check_api_key)])
def toggle_pin(article_id: str, req: PinReq, db: Session = Depends(get_db)):
    """Pin or unpin an article for quick access."""
    article = db.get(Article, article_id)
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    article.is_pinned = req.pinned
    db.commit()
    return {"article_id": article_id, "is_pinned": bool(article.is_pinned)}


class StoryboardReorderReq(BaseModel):
    scene_order: list[int]  # original scene_numbers in the desired new sequence


@app.patch("/articles/{article_id}/storyboard", dependencies=[Depends(check_api_key)])
def reorder_storyboard(
    article_id: str,
    req: StoryboardReorderReq,
    db: Session = Depends(get_db),
):
    """Reorder storyboard scenes. scene_order is the list of original scene_numbers in new sequence."""
    article = db.get(Article, article_id)
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
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
):
    """Replace a scene's image with a user-uploaded file (PNG/JPEG/WEBP, max 20 MB)."""
    article = db.get(Article, article_id)
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")

    content_type = (file.content_type or "").lower()
    if content_type not in _ALLOWED_IMAGE_TYPES:
        raise HTTPException(status_code=422, detail="Only PNG, JPEG, WEBP, or GIF images are accepted")

    contents = await file.read()
    if len(contents) > 20 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File too large — maximum 20 MB")

    image_dir = os.getenv("IMAGE_DIR", "/data/images")
    os.makedirs(image_dir, exist_ok=True)
    ext = _IMAGE_EXT_MAP.get(content_type, "png")
    save_path = os.path.join(image_dir, f"{article_id}_scene_{scene_number}_upload.{ext}")

    with open(save_path, "wb") as fh:
        fh.write(contents)

    existing = db.execute(
        select(ImageAsset).where(
            ImageAsset.article_id == article_id,
            ImageAsset.scene_number == scene_number,
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
):
    """Replace the article's TTS script. Existing audio/video assets are preserved."""
    article = db.get(Article, article_id)
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
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
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_optional_user),
):
    """Re-run script + storyboard generation from the article's existing raw_text."""
    article = db.get(Article, article_id)
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    if not article.raw_text:
        raise HTTPException(status_code=400, detail="Article has no raw text — cannot regenerate script")
    openai_key, _, _ = _resolve_user_keys(current_user, db)
    task = celery_app.send_task(
        "regenerate_script_for_article",
        kwargs={
            "article_id": article_id,
            "n_scenes": req.n_scenes,
            "openai_api_key": openai_key,
        },
    )
    return {"task_id": task.id, "status": "queued"}


@app.get("/articles/{article_id}/export.zip", dependencies=[Depends(check_api_key)])
def export_article_zip(article_id: str, db: Session = Depends(get_db)):
    """Download a ZIP bundle: script, audio, video, captions, and scene images."""
    article = db.get(Article, article_id)
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")

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
            .where(VideoAsset.article_id == article_id, VideoAsset.status == "ready")
            .order_by(VideoAsset.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        if video_row and os.path.exists(video_row.file_path):
            zf.write(video_row.file_path, "video.mp4")

        image_rows = db.execute(
            select(ImageAsset)
            .where(ImageAsset.article_id == article_id, ImageAsset.status == "ready")
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
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_optional_user),
):
    """Queue script + storyboard generation without TTS. Returns a task_id to poll.

    The resulting article_id can then be passed to POST /generate to run TTS
    on the user-reviewed (and optionally edited) script.
    """
    if not db.get(Source, req.source_id):
        raise HTTPException(status_code=404, detail="Unknown source_id")
    openai_key, _, _ = _resolve_user_keys(current_user, db)
    task = celery_app.send_task(
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
        },
    )
    return {"task_id": task.id, "status": "queued"}


@app.post("/generate", dependencies=[Depends(check_api_key)])
def generate(
    req: GenerateReq,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_optional_user),
):
    src = db.get(Source, req.source_id)
    if not src:
        raise HTTPException(status_code=404, detail="Unknown source_id")

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
    return {"task_id": task.id, "status": "queued"}


@app.get("/jobs/{task_id}", dependencies=[Depends(check_api_key)])
def job_status(task_id: str):
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
def cancel_job(task_id: str):
    """Revoke a queued or running task. Sends SIGTERM to the worker process."""
    celery_app.control.revoke(task_id, terminate=True, signal="SIGTERM")
    return {"cancelled": task_id}


@app.post("/generate-video", dependencies=[Depends(check_api_key)])
def generate_video(
    req: GenerateVideoReq,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_optional_user),
):
    article = db.get(Article, req.article_id)
    if not article:
        raise HTTPException(status_code=404, detail="Unknown article_id")
    if not article.storyboard_json:
        raise HTTPException(status_code=400, detail="Article has no storyboard — generate audio first")

    existing = _video_tasks.get(req.article_id)
    if existing:
        res = AsyncResult(existing, app=celery_app)
        if res.state in ("PENDING", "RECEIVED", "STARTED", "PROGRESS"):
            return {"task_id": existing, "status": "already_running"}

    openai_key, _, _ = _resolve_user_keys(current_user, db)
    task = celery_app.send_task(
        "generate_video_for_article",
        kwargs={
            "article_id": req.article_id,
            "audio_asset_id": req.audio_asset_id,
            "burn_subtitles": req.burn_subtitles,
            "openai_api_key": openai_key,
        },
    )
    _video_tasks[req.article_id] = task.id
    return {"task_id": task.id, "status": "queued"}


# ── Static assets ──────────────────────────────────────────────────────────

@app.get("/audio/{audio_id}", dependencies=[Depends(check_api_key)])
def get_audio(audio_id: str, db=Depends(get_db)):
    audio = db.get(AudioAsset, audio_id)
    if not audio:
        raise HTTPException(status_code=404, detail="Audio not found")
    if not os.path.exists(audio.file_path):
        raise HTTPException(status_code=404, detail="File missing on disk")
    return FileResponse(audio.file_path, media_type="audio/mpeg", filename=os.path.basename(audio.file_path))


@app.get("/image/{image_id}", dependencies=[Depends(check_api_key)])
def get_image(image_id: str, db=Depends(get_db)):
    img = db.get(ImageAsset, image_id)
    if not img:
        raise HTTPException(status_code=404, detail="Image not found")
    if img.status != "ready":
        raise HTTPException(status_code=409, detail=f"Image not ready: {img.status}")
    if not os.path.exists(img.file_path):
        raise HTTPException(status_code=404, detail="Image file missing on disk")
    return FileResponse(img.file_path, media_type="image/png")


class RegenerateReq(BaseModel):
    stage: Literal["video", "images"]
    burn_subtitles: bool = True


@app.post("/articles/{article_id}/regenerate", dependencies=[Depends(check_api_key)])
def regenerate_stage(
    article_id: str,
    req: RegenerateReq,
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
    if not article.storyboard_json:
        raise HTTPException(status_code=400, detail="Article has no storyboard — generate audio first")

    if req.stage == "images":
        db.execute(delete(ImageAsset).where(ImageAsset.article_id == article_id))
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
    return {"task_id": task.id, "status": "queued", "stage": req.stage}


@app.get("/video/{video_id}", dependencies=[Depends(check_api_key)])
def get_video(video_id: str, db=Depends(get_db)):
    video = db.get(VideoAsset, video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")
    if video.status != "ready":
        raise HTTPException(status_code=409, detail=f"Video not ready: status={video.status}")
    if not os.path.exists(video.file_path):
        raise HTTPException(status_code=404, detail="Video file missing on disk")
    return FileResponse(
        video.file_path,
        media_type="video/mp4",
        filename=os.path.basename(video.file_path),
    )


# ── Helpers ────────────────────────────────────────────────────────────────

def _resolve_user_keys(
    user: Optional[User],
    db: Session,
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Return (openai_key, elevenlabs_key, elevenlabs_voice_id) for the user.

    Returns (None, None, None) when no user is authenticated or user has no stored keys,
    in which case tasks fall back to server-level env var keys.
    """
    if not user:
        return None, None, None
    keys = db.get(UserApiKeys, user.id)
    if not keys:
        return None, None, None
    openai_key = decrypt_api_key(keys.openai_key_enc) if keys.openai_key_enc else None
    el_key = decrypt_api_key(keys.elevenlabs_key_enc) if keys.elevenlabs_key_enc else None
    return openai_key, el_key, keys.elevenlabs_voice_id


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
        .where(AudioAsset.article_id == article_id)
        .order_by(AudioAsset.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
