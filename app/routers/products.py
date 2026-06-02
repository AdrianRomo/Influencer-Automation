"""Product routes — browse, edit, delete, queue analysis, list generated concepts."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
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
from app.models import AdConcept, Product, User

router = APIRouter(tags=["products"])


def serialize_product(p: Product) -> dict:
    return {
        "id": p.id,
        "workspace_id": p.workspace_id,
        "brand_id": p.brand_id,
        "catalog_id": p.catalog_id,
        "external_id": p.external_id,
        "url": p.url,
        "title": p.title,
        "description": p.description,
        "price": p.price,
        "currency": p.currency,
        "category": p.category,
        "attributes_json": p.attributes_json,
        "primary_image_url": p.primary_image_url,
        "analysis_json": p.analysis_json,
        "status": p.status,
        "created_at": p.created_at.isoformat() if p.created_at else None,
    }


def serialize_concept(c: AdConcept) -> dict:
    return {
        "id": c.id,
        "workspace_id": c.workspace_id,
        "campaign_id": c.campaign_id,
        "product_id": c.product_id,
        "variant_index": c.variant_index,
        "angle": c.angle,
        "hook": c.hook,
        "headline": c.headline,
        "script_json": c.script_json,
        "captions_json": c.captions_json,
        "cta": c.cta,
        "on_screen_text": c.on_screen_text,
        "storyboard_json": c.storyboard_json,
        "compliance_json": c.compliance_json,
        "status": c.status,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }


def _get_owned_product(db: Session, product_id: str, user: User) -> Product:
    product = db.get(Product, product_id)
    if product is None or product.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Product not found")
    assert_workspace_member(db, product.workspace_id, user)
    return product


@router.get("/workspaces/{workspace_id}/products")
def list_products(
    workspace_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    assert_workspace_member(db, workspace_id, user)
    products = db.execute(
        select(Product).where(
            Product.workspace_id == workspace_id,
            Product.deleted_at.is_(None),
        ).order_by(Product.created_at.desc())
    ).scalars().all()
    return {"products": [serialize_product(p) for p in products]}


@router.get("/products/{product_id}")
def get_product(
    product_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return serialize_product(_get_owned_product(db, product_id, user))


class ProductUpdate(BaseModel):
    """Manual edits to an imported product. Only provided fields change."""
    title: Optional[str] = None
    description: Optional[str] = None
    price: Optional[float] = None
    currency: Optional[str] = None
    category: Optional[str] = None
    primary_image_url: Optional[str] = None


@router.patch("/products/{product_id}")
def update_product(
    product_id: str,
    body: ProductUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Edit an imported product's fields. Editing leaves any prior analysis in
    place; re-run Analyze to refresh it against the new copy."""
    product = _get_owned_product(db, product_id, user)
    data = body.model_dump(exclude_unset=True)
    if "title" in data and not (data["title"] or "").strip():
        raise HTTPException(status_code=422, detail="Title cannot be empty")
    for field, value in data.items():
        setattr(product, field, value)
    db.commit()
    return serialize_product(product)


@router.delete("/products/{product_id}")
def delete_product(
    product_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Soft-delete a product (drops it from lists; existing concepts are kept)."""
    product = _get_owned_product(db, product_id, user)
    product.deleted_at = datetime.utcnow()
    db.commit()
    return {"deleted": product_id}


@router.post("/products/{product_id}/analyze")
def analyze_product_endpoint(
    product_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Queue strategic analysis (audience/pains/benefits/angles) for a product."""
    import uuid as _uuid
    from app.tasks import celery_app
    from app import billing
    product = _get_owned_product(db, product_id, user)

    rate_limit(f"rl:analyze:{user.id}", limit=30, window=60)
    check_user_task_capacity(user.id)
    openai_key, _, _ = resolve_user_keys(user, db)

    amount = billing.cost("analyze")
    idem = str(_uuid.uuid4())
    try:
        billing.charge(db, product.workspace_id, amount, "analyze",
                       ref_type="product", ref_id=product.id, idempotency_key=idem)
    except billing.InsufficientCredits as exc:
        raise HTTPException(status_code=402, detail=str(exc))

    task = celery_app.send_task(
        "analyze_product",
        kwargs={
            "product_id": product.id, "openai_api_key": openai_key,
            "bill_workspace_id": product.workspace_id, "bill_amount": amount, "bill_idem": idem,
        },
    )
    store_task_owner(task.id, user.id)
    register_user_task(user.id, task.id)
    return {"task_id": task.id, "status": "queued", "credits_charged": amount}


@router.get("/products/{product_id}/concepts")
def list_product_concepts(
    product_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    product = _get_owned_product(db, product_id, user)
    concepts = db.execute(
        select(AdConcept).where(
            AdConcept.product_id == product.id,
            AdConcept.deleted_at.is_(None),
        ).order_by(AdConcept.variant_index)
    ).scalars().all()
    return {"concepts": [serialize_concept(c) for c in concepts]}
