"""Binary-asset routes: audio/image/video/scene-video/thumbnail downloads."""
from __future__ import annotations

import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import (
    assert_article_owner,
    check_api_key,
    get_optional_user,
)
from app.models import Article, AudioAsset, ImageAsset, SceneVideoAsset, User, VideoAsset

router = APIRouter()


@router.get("/audio/{audio_id}", dependencies=[Depends(check_api_key)])
def get_audio(audio_id: str, db: Session = Depends(get_db), current_user: Optional[User] = Depends(get_optional_user)):
    audio = db.get(AudioAsset, audio_id)
    if not audio or audio.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Audio not found")
    article = db.get(Article, audio.article_id)
    if article:
        assert_article_owner(article, current_user)
    if not os.path.exists(audio.file_path):
        raise HTTPException(status_code=404, detail="File missing on disk")
    return FileResponse(audio.file_path, media_type="audio/mpeg", filename=os.path.basename(audio.file_path))


@router.get("/image/{image_id}", dependencies=[Depends(check_api_key)])
def get_image(image_id: str, db: Session = Depends(get_db), current_user: Optional[User] = Depends(get_optional_user)):
    img = db.get(ImageAsset, image_id)
    if not img or img.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Image not found")
    article = db.get(Article, img.article_id)
    if article:
        assert_article_owner(article, current_user)
    if img.status != "ready":
        raise HTTPException(status_code=409, detail=f"Image not ready: {img.status}")
    if not os.path.exists(img.file_path):
        raise HTTPException(status_code=404, detail="Image file missing on disk")
    return FileResponse(img.file_path, media_type="image/png")


@router.get("/video/{video_id}", dependencies=[Depends(check_api_key)])
def get_video(video_id: str, db: Session = Depends(get_db), current_user: Optional[User] = Depends(get_optional_user)):
    video = db.get(VideoAsset, video_id)
    if not video or video.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Video not found")
    # Catalog-to-ad videos have no article; they're served by the workspace-scoped
    # /concepts/{id}/video route, not this article-centric one.
    if not video.article_id:
        raise HTTPException(status_code=404, detail="Video not found")
    article = db.get(Article, video.article_id)
    if article:
        assert_article_owner(article, current_user)
    if video.status != "ready":
        raise HTTPException(status_code=409, detail=f"Video not ready: status={video.status}")
    if not os.path.exists(video.file_path):
        raise HTTPException(status_code=404, detail="Video file missing on disk")
    return FileResponse(
        video.file_path,
        media_type="video/mp4",
        filename=os.path.basename(video.file_path),
    )


@router.get("/scene-videos/{scene_video_id}", dependencies=[Depends(check_api_key)])
def get_scene_video_clip(scene_video_id: str, db: Session = Depends(get_db), current_user: Optional[User] = Depends(get_optional_user)):
    """Download an individual animated scene clip."""
    sv = db.get(SceneVideoAsset, scene_video_id)
    if not sv or sv.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Scene video not found")
    article = db.get(Article, sv.article_id)
    if article:
        assert_article_owner(article, current_user)
    if sv.status not in ("ready", "fallback"):
        raise HTTPException(status_code=409, detail=f"Scene clip not ready: status={sv.status}")
    if not sv.file_path or not os.path.exists(sv.file_path):
        raise HTTPException(status_code=404, detail="Clip file missing on disk")
    return FileResponse(
        sv.file_path,
        media_type="video/mp4",
        filename=os.path.basename(sv.file_path),
    )


@router.get("/thumbnail/{article_id}", dependencies=[Depends(check_api_key)])
def get_thumbnail(article_id: str, db: Session = Depends(get_db), current_user: Optional[User] = Depends(get_optional_user)):
    """Serve the generated cover/thumbnail PNG for an article."""
    article = db.get(Article, article_id)
    if not article or not article.thumbnail_path:
        raise HTTPException(status_code=404, detail="Thumbnail not found")
    assert_article_owner(article, current_user)
    if not os.path.exists(article.thumbnail_path):
        raise HTTPException(status_code=404, detail="Thumbnail file missing from disk")
    return FileResponse(article.thumbnail_path, media_type="image/png")
