"""Editable-timeline + render routes for the embedded video editor.

  GET  /articles/{id}/timeline            load (build on first access) the Edit document
  PUT  /articles/{id}/timeline            save an edited Edit document (bumps version)
  POST /articles/{id}/render              submit a Shotstack render of the saved timeline
  GET  /articles/{id}/render/{job_id}     poll render; finalizes a VideoAsset when done
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.orm import Session

from app import render_shotstack
from app.db import get_db
from app.deps import assert_article_owner, check_api_key, get_optional_user
from app.models import Article, EditTimeline, User, VideoAsset
from app.timeline import build_timeline_from_article

logger = logging.getLogger(__name__)

router = APIRouter()


def _get_article(article_id: str, db: Session, user: Optional[User]) -> Article:
    article = db.get(Article, article_id)
    if not article or article.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Article not found")
    assert_article_owner(article, user)
    return article


def _active_timeline(db: Session, article_id: str) -> Optional[EditTimeline]:
    return (
        db.query(EditTimeline)
        .filter(EditTimeline.article_id == article_id, EditTimeline.deleted_at.is_(None))
        .order_by(EditTimeline.created_at.desc())
        .first()
    )


def _serialize(t: EditTimeline) -> dict:
    return {
        "timeline_id": t.id,
        "article_id": t.article_id,
        "version": t.version,
        "status": t.status,
        "render_provider": t.render_provider,
        "render_job_id": t.render_job_id,
        "video_asset_id": t.video_asset_id,
        "error": t.error,
        "edit": t.edit_json,
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
    }


@router.get("/articles/{article_id}/timeline", dependencies=[Depends(check_api_key)])
def get_timeline(
    article_id: str,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_optional_user),
):
    article = _get_article(article_id, db, current_user)
    t = _active_timeline(db, article_id)
    if t is None:
        # Build the first Edit document from the storyboard + existing assets.
        edit = build_timeline_from_article(db, article)
        t = EditTimeline(article_id=article_id, edit_json=edit, version=1, status="draft")
        db.add(t)
        db.commit()
        db.refresh(t)
    return _serialize(t)


@router.put("/articles/{article_id}/timeline", dependencies=[Depends(check_api_key)])
def save_timeline(
    article_id: str,
    payload: dict = Body(...),
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_optional_user),
):
    _get_article(article_id, db, current_user)
    edit = payload.get("edit")
    if not isinstance(edit, dict) or "tracks" not in edit:
        raise HTTPException(status_code=422, detail="Body must be {edit: {tracks: [...]}}")

    t = _active_timeline(db, article_id)
    if t is None:
        t = EditTimeline(article_id=article_id, edit_json=edit, version=1, status="draft")
        db.add(t)
    else:
        # Optimistic concurrency: reject stale saves when a version is supplied.
        client_version = payload.get("version")
        if client_version is not None and client_version != t.version:
            raise HTTPException(status_code=409, detail=f"Stale timeline (server v{t.version})")
        t.edit_json = edit
        t.version += 1
        t.status = "draft"
        t.error = None
        t.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(t)
    return _serialize(t)


@router.post("/articles/{article_id}/render", dependencies=[Depends(check_api_key)])
def start_render(
    article_id: str,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_optional_user),
):
    _get_article(article_id, db, current_user)
    if not render_shotstack.shotstack_enabled():
        raise HTTPException(
            status_code=503,
            detail="Shotstack rendering is not configured (set SHOTSTACK_API_KEY and "
                   "ASSET_STORAGE_BACKEND=r2).",
        )
    t = _active_timeline(db, article_id)
    if t is None:
        raise HTTPException(status_code=404, detail="No timeline to render; open the editor first")

    try:
        job_id = render_shotstack.submit_render(t.edit_json, db)
    except Exception as exc:  # surface provider/asset errors to the editor
        logger.exception("Render submit failed for article %s", article_id)
        t.status = "failed"
        t.error = str(exc)
        db.commit()
        raise HTTPException(status_code=502, detail=f"Render submit failed: {exc}")

    t.status = "rendering"
    t.render_provider = "shotstack"
    t.render_job_id = job_id
    t.video_asset_id = None
    t.error = None
    db.commit()
    return {"timeline_id": t.id, "render_job_id": job_id, "status": "rendering"}


@router.get("/articles/{article_id}/render/{job_id}", dependencies=[Depends(check_api_key)])
def poll_render(
    article_id: str,
    job_id: str,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_optional_user),
):
    article = _get_article(article_id, db, current_user)
    t = _active_timeline(db, article_id)
    if t is None or t.render_job_id != job_id:
        raise HTTPException(status_code=404, detail="Unknown render job for this article")

    # Already finalized — return the persisted video.
    if t.status == "rendered" and t.video_asset_id:
        return {"status": "done", "video_asset_id": t.video_asset_id}

    status = render_shotstack.poll_render(job_id)

    if status.failed:
        t.status = "failed"
        t.error = status.error or "render failed"
        db.commit()
        return {"status": "failed", "error": t.error}

    if not status.done:
        return {"status": status.state}

    # Done: download once, persist a VideoAsset, mark timeline rendered (idempotent).
    out_path = render_shotstack.render_output_path(t.id)
    render_shotstack.download_render(status.url, out_path)

    output = t.edit_json.get("output", {})
    has_subs = any(
        track.get("type") == "subtitles" and track.get("clips")
        for track in t.edit_json.get("tracks", [])
    )
    video = VideoAsset(
        article_id=article.id,
        file_path=out_path,
        format="mp4",
        duration_seconds=t.edit_json.get("duration"),
        width=int(output.get("width", 1080)),
        height=int(output.get("height", 1920)),
        has_subtitles=1 if has_subs else 0,
        render_mode="edited",
        status="ready",
    )
    db.add(video)
    db.flush()
    t.status = "rendered"
    t.video_asset_id = video.id
    t.error = None
    db.commit()
    return {"status": "done", "video_asset_id": video.id}
