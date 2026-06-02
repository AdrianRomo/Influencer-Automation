"""Catalog ingestion routes — CSV upload and product-URL import.

Thin HTTP layer over :func:`app.catalog.ingest_catalog`. Each route resolves +
scopes the workspace, then delegates to the ingestion service which runs the
right adapter and upserts products. Returns the import report (counts + errors).
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.catalog import ingest_catalog
from app.db import get_db
from app.deps import assert_workspace_member, get_current_user
from app.models import Catalog, Product, User

router = APIRouter(tags=["catalog"])

# Guardrail: cap upload size so a giant CSV can't exhaust memory.
_MAX_CSV_BYTES = 10 * 1024 * 1024  # 10 MB
_MAX_URLS = 50


class UrlIngestReq(BaseModel):
    urls: list[str] = Field(..., min_length=1, max_length=_MAX_URLS)
    brand_id: Optional[str] = None


def _serialize_catalog(c: Catalog) -> dict:
    return {
        "id": c.id,
        "workspace_id": c.workspace_id,
        "brand_id": c.brand_id,
        "source_type": c.source_type,
        "source_ref": c.source_ref,
        "status": c.status,
        "item_count": c.item_count,
        "last_synced_at": c.last_synced_at.isoformat() if c.last_synced_at else None,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }


@router.get("/workspaces/{workspace_id}/catalogs")
def list_catalogs(
    workspace_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    assert_workspace_member(db, workspace_id, user)
    catalogs = db.execute(
        select(Catalog).where(Catalog.workspace_id == workspace_id).order_by(Catalog.created_at.desc())
    ).scalars().all()
    return {"catalogs": [_serialize_catalog(c) for c in catalogs]}


@router.post("/workspaces/{workspace_id}/catalogs/csv", status_code=201)
def ingest_csv(
    workspace_id: str,
    file: UploadFile = File(...),
    brand_id: Optional[str] = Form(default=None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Upload a CSV catalog. Columns auto-detected (Shopify/Woo/Merchant exports)."""
    assert_workspace_member(db, workspace_id, user)
    content = file.file.read(_MAX_CSV_BYTES + 1)
    if len(content) > _MAX_CSV_BYTES:
        raise HTTPException(status_code=413, detail="CSV exceeds 10 MB limit")
    if not content:
        raise HTTPException(status_code=422, detail="Empty file")

    report = ingest_catalog(
        db,
        workspace_id=workspace_id,
        source_type="csv",
        brand_id=brand_id,
        source_ref=file.filename,
        content=content,
    )
    return report


@router.post("/workspaces/{workspace_id}/catalogs/url", status_code=201)
def ingest_urls(
    workspace_id: str,
    body: UrlIngestReq,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Scrape one or more product-page URLs into the catalog."""
    assert_workspace_member(db, workspace_id, user)
    report = ingest_catalog(
        db,
        workspace_id=workspace_id,
        source_type="url",
        brand_id=body.brand_id,
        source_ref=f"{len(body.urls)} url(s)",
        urls=body.urls,
    )
    return report


@router.get("/catalogs/{catalog_id}/products")
def list_catalog_products(
    catalog_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    catalog = db.get(Catalog, catalog_id)
    if catalog is None:
        raise HTTPException(status_code=404, detail="Catalog not found")
    assert_workspace_member(db, catalog.workspace_id, user)
    products = db.execute(
        select(Product).where(
            Product.catalog_id == catalog_id,
            Product.deleted_at.is_(None),
        ).order_by(Product.created_at)
    ).scalars().all()
    from app.routers.products import serialize_product
    return {"products": [serialize_product(p) for p in products]}
