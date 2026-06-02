"""Ad-concept routes — inline edit, recheck, regenerate, delete (the edit loop)."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import (
    assert_workspace_member,
    check_user_task_capacity,
    get_current_user,
    rate_limit,
    register_user_task,
    resolve_user_keys,
    store_task_owner,
)
from app.models import AdConcept, Brand, Product, User
from app.routers.products import serialize_concept
from app.usage import UsageCollector

router = APIRouter(tags=["concepts"])


class ConceptUpdate(BaseModel):
    """All fields optional — only provided keys are changed. ``recheck`` re-runs
    the compliance pass on the edited copy (a single fast LLM call)."""
    angle: Optional[str] = None
    hook: Optional[str] = None
    headline: Optional[str] = None
    cta: Optional[str] = None
    on_screen_text: Optional[str] = None
    script_json: Optional[dict] = None
    captions_json: Optional[dict] = None
    recheck: bool = False


class RegenerateReq(BaseModel):
    angle: Optional[str] = Field(default=None, max_length=300)


def _get_owned_concept(db: Session, concept_id: str, user: User) -> AdConcept:
    concept = db.get(AdConcept, concept_id)
    if concept is None or concept.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Concept not found")
    assert_workspace_member(db, concept.workspace_id, user)
    return concept


@router.get("/concepts/{concept_id}")
def get_concept(
    concept_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return serialize_concept(_get_owned_concept(db, concept_id, user))


@router.patch("/concepts/{concept_id}")
def update_concept(
    concept_id: str,
    body: ConceptUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Save manual edits to a concept. With ``recheck=true`` also re-runs the
    compliance pass on the edited copy so the risk badge reflects the change."""
    concept = _get_owned_concept(db, concept_id, user)

    data = body.model_dump(exclude_unset=True)
    data.pop("recheck", None)
    editable = {"angle", "hook", "headline", "cta", "on_screen_text", "script_json", "captions_json"}
    changed = False
    for field, value in data.items():
        if field in editable:
            setattr(concept, field, value)
            changed = True

    if changed:
        concept.status = "edited"

    # Optional synchronous compliance recheck on the edited copy.
    if body.recheck:
        from app.ad_concepts import run_compliance
        product = db.get(Product, concept.product_id)
        prohibited_words: list = []
        category = product.category if product else None
        if product and product.brand_id:
            brand = db.get(Brand, product.brand_id)
            if brand:
                prohibited_words = brand.prohibited_words or []

        openai_key, _, _ = resolve_user_keys(user, db)
        collector = UsageCollector(
            workspace_id=concept.workspace_id,
            product_id=concept.product_id,
            ad_concept_id=concept.id,
        )
        pack = {
            "hook": concept.hook, "headline": concept.headline, "cta": concept.cta,
            "on_screen_text": concept.on_screen_text,
            "scripts": concept.script_json or {}, "captions": concept.captions_json or {},
        }
        compliance = run_compliance(
            pack, category=category, prohibited_words=prohibited_words,
            api_key=openai_key, collector=collector,
        )
        concept.compliance_json = compliance
        concept.status = "flagged" if compliance.get("risk") == "high" else "edited"
        db.commit()
        collector.flush(db)
    else:
        db.commit()

    return serialize_concept(concept)


@router.post("/concepts/{concept_id}/regenerate")
def regenerate_concept(
    concept_id: str,
    body: RegenerateReq,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Queue a full regeneration of this concept (fresh creative + compliance)."""
    import uuid as _uuid
    from app.tasks import celery_app
    from app import billing
    concept = _get_owned_concept(db, concept_id, user)

    rate_limit(f"rl:regen_concept:{user.id}", limit=30, window=60)
    check_user_task_capacity(user.id)
    openai_key, _, _ = resolve_user_keys(user, db)

    amount = billing.cost("concept")
    idem = str(_uuid.uuid4())
    try:
        billing.charge(db, concept.workspace_id, amount, "concept",
                       ref_type="concept", ref_id=concept.id, idempotency_key=idem)
    except billing.InsufficientCredits as exc:
        raise HTTPException(status_code=402, detail=str(exc))

    task = celery_app.send_task(
        "regenerate_ad_concept",
        kwargs={
            "concept_id": concept.id, "angle": body.angle, "openai_api_key": openai_key,
            "bill_workspace_id": concept.workspace_id, "bill_amount": amount, "bill_idem": idem,
        },
    )
    store_task_owner(task.id, user.id)
    register_user_task(user.id, task.id)
    return {"task_id": task.id, "status": "queued", "credits_charged": amount}


class ConceptVideoReq(BaseModel):
    platform: str = "tiktok"
    burn_subtitles: bool = True


@router.post("/concepts/{concept_id}/video")
def generate_concept_video(
    concept_id: str,
    body: ConceptVideoReq,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Queue an MP4 render from this concept's storyboard (images + voiceover)."""
    import uuid as _uuid
    from app.tasks import celery_app
    from app import billing
    concept = _get_owned_concept(db, concept_id, user)
    if not concept.storyboard_json:
        raise HTTPException(status_code=409, detail="Concept has no storyboard")

    rate_limit(f"rl:concept_video:{user.id}", limit=10, window=60)
    check_user_task_capacity(user.id)
    openai_key, el_key, el_voice = resolve_user_keys(user, db)

    amount = billing.cost("video")
    idem = str(_uuid.uuid4())
    try:
        billing.charge(db, concept.workspace_id, amount, "video",
                       ref_type="concept", ref_id=concept.id, idempotency_key=idem)
    except billing.InsufficientCredits as exc:
        raise HTTPException(status_code=402, detail=str(exc))

    task = celery_app.send_task(
        "generate_ad_concept_video",
        kwargs={
            "concept_id": concept.id,
            "platform": body.platform,
            "burn_subtitles": body.burn_subtitles,
            "voice_id": el_voice,
            "openai_api_key": openai_key,
            "elevenlabs_api_key": el_key,
            "bill_workspace_id": concept.workspace_id,
            "bill_amount": amount,
            "bill_idem": idem,
        },
    )
    store_task_owner(task.id, user.id)
    register_user_task(user.id, task.id)
    return {"task_id": task.id, "status": "queued", "credits_charged": amount}


@router.get("/concepts/{concept_id}/video")
def download_concept_video(
    concept_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Stream the latest ready video for a concept (workspace-scoped)."""
    import os
    from fastapi.responses import FileResponse
    from sqlalchemy import select as _select
    from app.models import VideoAsset

    concept = _get_owned_concept(db, concept_id, user)
    video = db.execute(
        _select(VideoAsset).where(
            VideoAsset.ad_concept_id == concept.id,
            VideoAsset.status == "ready",
            VideoAsset.deleted_at.is_(None),
        ).order_by(VideoAsset.created_at.desc()).limit(1)
    ).scalar_one_or_none()
    if not video:
        raise HTTPException(status_code=404, detail="No ready video for this concept")
    if not os.path.exists(video.file_path):
        raise HTTPException(status_code=404, detail="Video file missing on disk")
    return FileResponse(video.file_path, media_type="video/mp4", filename=os.path.basename(video.file_path))


@router.delete("/concepts/{concept_id}")
def delete_concept(
    concept_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Soft-delete a concept (excluded from lists + exports)."""
    concept = _get_owned_concept(db, concept_id, user)
    concept.deleted_at = datetime.utcnow()
    db.commit()
    return {"deleted": concept_id}
