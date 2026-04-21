import os
from contextlib import asynccontextmanager
from datetime import datetime
from fastapi import FastAPI, Depends, HTTPException, Header, Query
from fastapi.responses import FileResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from celery.result import AsyncResult
from sqlalchemy import select, func, delete
from sqlalchemy.orm import Session
from typing import Literal, Optional

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
    ImageAssetRef, VideoAssetRef,
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


class GenerateReq(BaseModel):
    source_id: str
    voice_id: str | None = None
    target_seconds: int = Field(default=DEFAULT_TARGET_SECONDS, ge=30, le=600)
    n_scenes: int = Field(default=DEFAULT_SCENES, ge=0, le=20)


class GenerateVideoReq(BaseModel):
    article_id: str
    audio_asset_id: str | None = None
    burn_subtitles: bool = True


# ── Health ─────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"ok": True}


# ── Auth ───────────────────────────────────────────────────────────────────

@app.post("/auth/register", response_model=TokenResp)
def register(req: RegisterReq, db: Session = Depends(get_db)):
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
def login(req: LoginReq, db: Session = Depends(get_db)):
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
        .order_by(Article.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    if source_id:
        q = q.where(Article.source_id == source_id)
    if current_user:
        q = q.where(Article.user_id == current_user.id)

    rows = db.execute(q).all()
    return [
        {
            "id": r.Article.id,
            "title": r.Article.title,
            "url": r.Article.url,
            "source_id": r.Article.source_id,
            "created_at": r.Article.created_at,
            "has_audio": bool(r.audio_count),
            "has_video": bool(r.video_count),
        }
        for r in rows
    ]


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


# ── Jobs ───────────────────────────────────────────────────────────────────

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
