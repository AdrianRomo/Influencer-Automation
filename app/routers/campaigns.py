"""Campaign routes — group products by goal, then generate ad concepts."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
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
from app.models import AdConcept, Campaign, Product, User
from app.routers.products import serialize_concept

router = APIRouter(tags=["campaigns"])

_GOALS = {"awareness", "conversion", "ugc"}
_MAX_PRODUCTS_PER_RUN = 25  # cost guardrail; bump behind credits later


class CampaignCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    goal: str = "awareness"
    platforms: list[str] = Field(default_factory=lambda: ["tiktok"])
    product_ids: list[str] = Field(default_factory=list)
    brand_id: Optional[str] = None


class GenerateReq(BaseModel):
    n_variants: int = Field(default=3, ge=1, le=10)
    product_ids: Optional[list[str]] = None  # subset; defaults to campaign's list


def _serialize_campaign(c: Campaign) -> dict:
    return {
        "id": c.id,
        "workspace_id": c.workspace_id,
        "brand_id": c.brand_id,
        "name": c.name,
        "goal": c.goal,
        "platforms": c.platforms or [],
        "product_ids": c.product_ids or [],
        "status": c.status,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }


def _get_owned_campaign(db: Session, campaign_id: str, user: User) -> Campaign:
    campaign = db.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    assert_workspace_member(db, campaign.workspace_id, user)
    return campaign


@router.get("/workspaces/{workspace_id}/campaigns")
def list_campaigns(
    workspace_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    assert_workspace_member(db, workspace_id, user)
    campaigns = db.execute(
        select(Campaign).where(Campaign.workspace_id == workspace_id).order_by(Campaign.created_at.desc())
    ).scalars().all()
    return {"campaigns": [_serialize_campaign(c) for c in campaigns]}


@router.post("/workspaces/{workspace_id}/campaigns", status_code=201)
def create_campaign(
    workspace_id: str,
    body: CampaignCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    assert_workspace_member(db, workspace_id, user)
    if body.goal not in _GOALS:
        raise HTTPException(status_code=422, detail=f"goal must be one of {sorted(_GOALS)}")

    # Only accept product ids that belong to this workspace.
    valid_ids: list[str] = []
    if body.product_ids:
        valid_ids = db.execute(
            select(Product.id).where(
                Product.id.in_(body.product_ids),
                Product.workspace_id == workspace_id,
            )
        ).scalars().all()

    campaign = Campaign(
        workspace_id=workspace_id,
        brand_id=body.brand_id,
        name=body.name,
        goal=body.goal,
        platforms=body.platforms,
        product_ids=valid_ids,
    )
    db.add(campaign)
    db.commit()
    return _serialize_campaign(campaign)


@router.post("/campaigns/{campaign_id}/generate")
def generate_campaign_concepts(
    campaign_id: str,
    body: GenerateReq,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Queue ad-concept generation for the campaign's products (one task each)."""
    from app.tasks import celery_app
    campaign = _get_owned_campaign(db, campaign_id, user)

    target_ids = body.product_ids or campaign.product_ids or []
    # Re-scope to workspace to prevent injecting foreign product ids via the body.
    target_ids = db.execute(
        select(Product.id).where(
            Product.id.in_(target_ids),
            Product.workspace_id == campaign.workspace_id,
            Product.deleted_at.is_(None),
        )
    ).scalars().all()
    if not target_ids:
        raise HTTPException(status_code=422, detail="No valid products to generate for")
    if len(target_ids) > _MAX_PRODUCTS_PER_RUN:
        raise HTTPException(
            status_code=422,
            detail=f"Too many products in one run (max {_MAX_PRODUCTS_PER_RUN})",
        )

    rate_limit(f"rl:gen_concepts:{user.id}", limit=10, window=60)
    check_user_task_capacity(user.id)
    openai_key, _, _ = resolve_user_keys(user, db)

    # Pre-check the whole run so we don't charge some products then run dry.
    import uuid as _uuid
    from app import billing
    per_product = body.n_variants * billing.cost("concept")
    total = per_product * len(target_ids)
    if billing.get_balance(db, campaign.workspace_id) < total:
        raise HTTPException(
            status_code=402,
            detail=f"Insufficient credits: need {total}, have {billing.get_balance(db, campaign.workspace_id)}",
        )

    tasks: list[dict] = []
    for pid in target_ids:
        idem = str(_uuid.uuid4())
        try:
            billing.charge(db, campaign.workspace_id, per_product, "concept",
                           ref_type="product", ref_id=pid, idempotency_key=idem)
        except billing.InsufficientCredits as exc:
            break  # ran dry mid-batch (concurrent spend) — queue what we paid for
        task = celery_app.send_task(
            "generate_ad_concepts",
            kwargs={
                "product_id": pid,
                "campaign_id": campaign.id,
                "n_variants": body.n_variants,
                "openai_api_key": openai_key,
                "bill_workspace_id": campaign.workspace_id,
                "bill_amount": per_product,
                "bill_idem": idem,
            },
        )
        store_task_owner(task.id, user.id)
        register_user_task(user.id, task.id)
        tasks.append({"product_id": pid, "task_id": task.id})

    campaign.status = "generating"
    db.commit()
    return {"campaign_id": campaign.id, "tasks": tasks, "count": len(tasks), "credits_charged": per_product * len(tasks)}


@router.get("/campaigns/{campaign_id}/export")
def export_campaign(
    campaign_id: str,
    format: str = "json",
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Download the campaign's full creative library as json | csv | zip."""
    from fastapi.responses import Response
    from app.export import build_export

    campaign = _get_owned_campaign(db, campaign_id, user)
    try:
        content, media_type, filename = build_export(db, campaign, format)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/campaigns/{campaign_id}/concepts")
def list_campaign_concepts(
    campaign_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    campaign = _get_owned_campaign(db, campaign_id, user)
    concepts = db.execute(
        select(AdConcept).where(
            AdConcept.campaign_id == campaign.id,
            AdConcept.deleted_at.is_(None),
        ).order_by(AdConcept.product_id, AdConcept.variant_index)
    ).scalars().all()
    return {"concepts": [serialize_concept(c) for c in concepts]}
