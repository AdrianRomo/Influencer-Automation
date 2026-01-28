import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from celery.result import AsyncResult
from sqlalchemy import select

from app.db import get_db, engine
from app.models import Base, Source, AudioAsset, Article
from app.rss_sources import SOURCES
from app.tasks import celery_app

Base.metadata.create_all(bind=engine)

DEFAULT_TARGET_SECONDS = int(os.getenv("TTS_TARGET_SECONDS", "180"))
DEFAULT_SCENES = int(os.getenv("STORYBOARD_SCENES", "8"))

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Seed RSS sources at startup
    from sqlalchemy.orm import Session
    with Session(engine) as db:
        for s in SOURCES:
            if not db.get(Source, s["id"]):
                db.add(Source(**s))
        db.commit()
    yield

app = FastAPI(
    title="Medical RSS → Summary → ElevenLabs Audio (MVP)",
    lifespan=lifespan,
)

class GenerateReq(BaseModel):
    source_id: str
    voice_id: str | None = None
    target_seconds: int = Field(default=DEFAULT_TARGET_SECONDS, ge=30, le=600)
    n_scenes: int = Field(default=DEFAULT_SCENES, ge=0, le=20)

@app.get("/health")
def health():
    return {"ok": True}

@app.get("/sources")
def list_sources(db=Depends(get_db)):
    rows = db.execute(select(Source)).scalars().all()
    return [
        {"id": r.id, "name": r.name, "rss_url": r.rss_url, "language_hint": r.language_hint}
        for r in rows
    ]

@app.post("/generate")
def generate(req: GenerateReq, db=Depends(get_db)):
    # Validate source exists before queueing
    src = db.get(Source, req.source_id)
    if not src:
        raise HTTPException(status_code=404, detail="Unknown source_id")

    # Prefer kwargs so it stays stable as you add params
    task = celery_app.send_task(
        "generate_latest_for_source",
        kwargs={
            "source_id": req.source_id,
            "voice_id": req.voice_id,
            "target_seconds": req.target_seconds,
            "n_scenes": req.n_scenes,
        },
    )
    return {"task_id": task.id, "status": "queued"}

@app.get("/jobs/{task_id}")
def job_status(task_id: str):
    res = AsyncResult(task_id, app=celery_app)
    payload = {"task_id": task_id, "state": res.state}

    if res.successful():
        payload["result"] = res.result

        # If your task returns audio_id, expose a friendly URL too
        audio_id = (res.result or {}).get("audio_id")
        if audio_id:
            payload["result"]["audio_url"] = f"/audio/{audio_id}"

    elif res.failed():
        payload["error"] = str(res.result)

    return payload

@app.get("/audio/{audio_id}")
def get_audio(audio_id: str, db=Depends(get_db)):
    audio = db.get(AudioAsset, audio_id)
    if not audio:
        raise HTTPException(status_code=404, detail="Audio not found")
    if not os.path.exists(audio.file_path):
        raise HTTPException(status_code=404, detail="File missing on disk")
    return FileResponse(audio.file_path, media_type="audio/mpeg", filename=os.path.basename(audio.file_path))

@app.get("/articles/{article_id}")
def get_article(article_id: str, db=Depends(get_db)):
    article = db.get(Article, article_id)
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")

    out = {
        "id": article.id,
        "source_id": article.source_id,
        "title": article.title,
        "url": article.url,
        "published_at": article.published_at,
        "created_at": article.created_at,
        "tts_script": article.tts_script,
    }

    # If you added storyboard_json to the model
    if hasattr(article, "storyboard_json"):
        out["storyboard"] = article.storyboard_json

    return out
