"""Brand routes — brand voice + compliance config that drives ad generation."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import assert_workspace_member, assert_workspace_resource, get_current_user
from app.models import Brand, User

router = APIRouter(tags=["brands"])


class BrandCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    site_url: Optional[str] = None
    tone_json: Optional[dict] = None
    target_audience_json: Optional[dict] = None
    brand_voice_json: Optional[dict] = None
    prohibited_words: Optional[list[str]] = None
    claim_policy_json: Optional[dict] = None
    ai_disclosure: bool = False
    default_language: str = "en"


class BrandUpdate(BaseModel):
    name: Optional[str] = None
    site_url: Optional[str] = None
    tone_json: Optional[dict] = None
    target_audience_json: Optional[dict] = None
    brand_voice_json: Optional[dict] = None
    prohibited_words: Optional[list[str]] = None
    claim_policy_json: Optional[dict] = None
    ai_disclosure: Optional[bool] = None
    default_language: Optional[str] = None


def _serialize(b: Brand) -> dict:
    return {
        "id": b.id,
        "workspace_id": b.workspace_id,
        "name": b.name,
        "site_url": b.site_url,
        "tone_json": b.tone_json,
        "target_audience_json": b.target_audience_json,
        "brand_voice_json": b.brand_voice_json,
        "prohibited_words": b.prohibited_words or [],
        "claim_policy_json": b.claim_policy_json,
        "ai_disclosure": bool(b.ai_disclosure),
        "default_language": b.default_language,
        "created_at": b.created_at.isoformat() if b.created_at else None,
    }


@router.get("/workspaces/{workspace_id}/brands")
def list_brands(
    workspace_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    assert_workspace_member(db, workspace_id, user)
    brands = db.execute(
        select(Brand).where(Brand.workspace_id == workspace_id).order_by(Brand.created_at)
    ).scalars().all()
    return {"brands": [_serialize(b) for b in brands]}


@router.post("/workspaces/{workspace_id}/brands", status_code=201)
def create_brand(
    workspace_id: str,
    body: BrandCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    assert_workspace_member(db, workspace_id, user)
    brand = Brand(
        workspace_id=workspace_id,
        name=body.name,
        site_url=body.site_url,
        tone_json=body.tone_json,
        target_audience_json=body.target_audience_json,
        brand_voice_json=body.brand_voice_json,
        prohibited_words=body.prohibited_words,
        claim_policy_json=body.claim_policy_json,
        ai_disclosure=1 if body.ai_disclosure else 0,
        default_language=body.default_language,
    )
    db.add(brand)
    db.commit()
    return _serialize(brand)


@router.get("/brands/{brand_id}")
def get_brand(
    brand_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    brand = db.get(Brand, brand_id)
    if brand is not None:
        assert_workspace_member(db, brand.workspace_id, user)
    if brand is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Brand not found")
    return _serialize(brand)


@router.patch("/brands/{brand_id}")
def update_brand(
    brand_id: str,
    body: BrandUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    brand = db.get(Brand, brand_id)
    if brand is not None:
        assert_workspace_member(db, brand.workspace_id, user)
    if brand is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Brand not found")

    data = body.model_dump(exclude_unset=True)
    if "ai_disclosure" in data and data["ai_disclosure"] is not None:
        brand.ai_disclosure = 1 if data.pop("ai_disclosure") else 0
    for field, value in data.items():
        setattr(brand, field, value)
    db.commit()
    return _serialize(brand)
