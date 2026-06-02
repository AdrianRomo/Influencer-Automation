"""Catalog ingestion service.

Turns a stream of :class:`~app.ingest.base.NormalizedProduct` records (from any
adapter) into ``products`` rows with idempotent upsert, mirroring the article
``(source_id, url)`` dedupe pattern. ``ingest_catalog`` is the high-level entry
point used by the API/Celery: it creates a ``Catalog`` row, runs the adapter,
upserts the results, and returns an import report (counts + per-row errors).
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ingest import NormalizedProduct, get_adapter
from app.models import Catalog, Product, ProductImage

logger = logging.getLogger(__name__)


def _find_existing(
    db: Session,
    *,
    workspace_id: str,
    catalog_id: Optional[str],
    np: NormalizedProduct,
) -> Optional[Product]:
    """Locate an existing product to update/restore, by the strongest available key.

    Priority: (catalog_id, external_id) → (workspace_id, url) → (workspace_id, title).
    Matches active AND soft-deleted rows (a deleted row must be found so it can be
    restored). When several rows match a weak key, prefer the active one, then the
    most recent — so we never raise on duplicates and don't resurrect the wrong row.
    """
    def _pick(stmt):
        # Active first (deleted_at IS NULL), then newest.
        rows = db.execute(
            stmt.order_by(Product.deleted_at.is_(None).desc(), Product.created_at.desc())
        ).scalars().all()
        return rows[0] if rows else None

    if np.external_id and catalog_id:
        found = _pick(select(Product).where(
            Product.catalog_id == catalog_id,
            Product.external_id == np.external_id,
        ))
        if found:
            return found

    if np.url:
        found = _pick(select(Product).where(
            Product.workspace_id == workspace_id,
            Product.url == np.url,
        ))
        if found:
            return found

    return _pick(select(Product).where(
        Product.workspace_id == workspace_id,
        Product.title == np.title,
    ))


def _apply_fields(product: Product, np: NormalizedProduct) -> None:
    """Copy normalized fields onto a Product, never overwriting with empties."""
    product.title = np.title  # always present (validated)
    if np.external_id:
        product.external_id = np.external_id
    if np.url:
        product.url = np.url
    if np.description:
        product.description = np.description
    if np.price is not None:
        product.price = np.price
    if np.currency:
        product.currency = np.currency
    if np.category:
        product.category = np.category
    if np.attributes:
        product.attributes_json = np.attributes
    if np.primary_image_url:
        product.primary_image_url = np.primary_image_url


def upsert_products(
    db: Session,
    *,
    workspace_id: str,
    products: list[NormalizedProduct],
    catalog_id: Optional[str] = None,
    brand_id: Optional[str] = None,
) -> dict:
    """Insert or update products. Returns {created, updated, product_ids}.

    Commits once at the end. Re-analysis is NOT triggered here — callers queue
    ``analyze_product`` per returned id.
    """
    created = 0
    updated = 0
    restored = 0
    product_ids: list[str] = []
    # Track ids touched in THIS run so a duplicate CSV row resolves to the row we
    # just inserted/restored rather than creating a second copy.
    seen_ids: set[str] = set()

    for np in products:
        if not np.is_valid():
            continue

        # Dedup matches BOTH active and soft-deleted rows on purpose: a deleted
        # row must be restored (not re-inserted), otherwise the unique
        # constraint on (catalog_id, external_id) would block the re-import.
        existing = _find_existing(
            db, workspace_id=workspace_id, catalog_id=catalog_id, np=np
        )

        if existing is not None:
            was_deleted = existing.deleted_at is not None
            _apply_fields(existing, np)
            if was_deleted:
                # Resurrect: clear the soft-delete and re-home to this upload's
                # catalog so the re-imported item shows up again.
                existing.deleted_at = None
                existing.catalog_id = catalog_id or existing.catalog_id
                existing.brand_id = brand_id or existing.brand_id
                # Keep prior analysis if it survived; reflect it in status.
                existing.status = "analyzed" if existing.analysis_json else "created"
                restored += 1
            else:
                if catalog_id and not existing.catalog_id:
                    existing.catalog_id = catalog_id
                if brand_id and not existing.brand_id:
                    existing.brand_id = brand_id
                updated += 1
            seen_ids.add(existing.id)
            product_ids.append(existing.id)
        else:
            product = Product(
                workspace_id=workspace_id,
                brand_id=brand_id,
                catalog_id=catalog_id,
            )
            _apply_fields(product, np)
            db.add(product)
            db.flush()  # assign id before creating child image rows

            # Persist all images; first becomes primary.
            for idx, url in enumerate(np.image_urls):
                db.add(ProductImage(
                    product_id=product.id,
                    url=url,
                    is_primary=1 if idx == 0 else 0,
                ))

            created += 1
            seen_ids.add(product.id)
            product_ids.append(product.id)

    db.commit()
    return {
        "created": created,
        "updated": updated,
        "restored": restored,
        "product_ids": product_ids,
    }


def ingest_catalog(
    db: Session,
    *,
    workspace_id: str,
    source_type: str,
    brand_id: Optional[str] = None,
    source_ref: Optional[str] = None,
    **adapter_kwargs,
) -> dict:
    """Run an ingestion adapter end-to-end and persist the results.

    Creates a Catalog row, runs the adapter, upserts products, records the
    item count + status, and returns an import report:
        {catalog_id, source_type, created, updated, product_ids, errors, item_count}

    Adapter kwargs are passed through, e.g.:
        ingest_catalog(db, workspace_id=ws, source_type="csv", content=file_bytes)
        ingest_catalog(db, workspace_id=ws, source_type="url", urls=[...])
    """
    catalog = Catalog(
        workspace_id=workspace_id,
        brand_id=brand_id,
        source_type=source_type,
        source_ref=source_ref,
        status="processing",
    )
    db.add(catalog)
    db.commit()

    report: dict = {
        "catalog_id": catalog.id,
        "source_type": source_type,
        "created": 0,
        "updated": 0,
        "restored": 0,
        "product_ids": [],
        "errors": [],
        "item_count": 0,
    }

    try:
        adapter = get_adapter(source_type, **adapter_kwargs)
        products = adapter.collect()
        report["errors"] = list(adapter.errors)

        result = upsert_products(
            db,
            workspace_id=workspace_id,
            products=products,
            catalog_id=catalog.id,
            brand_id=brand_id,
        )
        report.update(result)
        report["item_count"] = len(result["product_ids"])

        catalog.item_count = report["item_count"]
        catalog.status = "ready" if report["item_count"] else "empty"
        catalog.last_synced_at = datetime.utcnow()
        db.commit()
    except Exception as exc:
        logger.exception("Catalog ingestion failed for source_type=%s", source_type)
        catalog.status = "failed"
        db.commit()
        report["errors"].append(f"Ingestion failed: {exc!r}")

    return report
