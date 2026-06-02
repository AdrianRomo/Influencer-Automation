"""Creative-library export builder.

Turns a campaign's generated ad concepts into a downloadable deliverable in three
formats:

- ``json`` — full structured dump (campaign + per-product concepts), for re-import
  or programmatic use.
- ``csv``  — one flat row per concept, for spreadsheets / bulk review / ad tools.
- ``zip``  — a human-friendly creative library: a per-product folder of readable
  ``.txt`` briefs plus ``campaign.json`` and ``all_concepts.csv`` at the root.

Pure functions returning ``bytes``; the router wraps them in a streaming response.
"""
from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AdConcept, Campaign, Product

# Columns for the flat CSV. Order matters — it's the spreadsheet header.
_CSV_COLUMNS = [
    "product_title", "product_id", "variant_index", "status", "compliance_risk",
    "angle", "hook", "headline", "cta", "on_screen_text",
    "script_ugc", "script_demo", "script_influencer", "captions_json",
]


def _slug(text: str, fallback: str = "item") -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", (text or "").strip().lower()).strip("-")
    return s[:60] or fallback


def gather(db: Session, campaign: Campaign) -> dict:
    """Collect the campaign, its concepts, and the products they belong to."""
    concepts = db.execute(
        select(AdConcept).where(
            AdConcept.campaign_id == campaign.id,
            AdConcept.deleted_at.is_(None),
        ).order_by(AdConcept.product_id, AdConcept.variant_index)
    ).scalars().all()

    product_ids = {c.product_id for c in concepts}
    products: dict[str, Product] = {}
    if product_ids:
        rows = db.execute(
            select(Product).where(Product.id.in_(product_ids))
        ).scalars().all()
        products = {p.id: p for p in rows}

    return {"campaign": campaign, "concepts": concepts, "products": products}


def _concept_row(c: AdConcept, products: dict[str, Product]) -> dict:
    product = products.get(c.product_id)
    scripts = c.script_json or {}
    compliance = c.compliance_json or {}
    return {
        "product_title": product.title if product else "",
        "product_id": c.product_id,
        "variant_index": c.variant_index,
        "status": c.status,
        "compliance_risk": compliance.get("risk", ""),
        "angle": c.angle or "",
        "hook": c.hook or "",
        "headline": c.headline or "",
        "cta": c.cta or "",
        "on_screen_text": c.on_screen_text or "",
        "script_ugc": scripts.get("ugc", ""),
        "script_demo": scripts.get("demo", ""),
        "script_influencer": scripts.get("influencer", ""),
        "captions_json": json.dumps(c.captions_json or {}, ensure_ascii=False),
    }


def _concept_dict(c: AdConcept) -> dict:
    return {
        "id": c.id,
        "product_id": c.product_id,
        "variant_index": c.variant_index,
        "status": c.status,
        "angle": c.angle,
        "hook": c.hook,
        "headline": c.headline,
        "scripts": c.script_json or {},
        "captions": c.captions_json or {},
        "cta": c.cta,
        "on_screen_text": c.on_screen_text,
        "storyboard": c.storyboard_json or {},
        "compliance": c.compliance_json or {},
    }


def export_json(data: dict) -> bytes:
    campaign = data["campaign"]
    payload = {
        "campaign": {
            "id": campaign.id,
            "name": campaign.name,
            "goal": campaign.goal,
            "platforms": campaign.platforms or [],
        },
        "concepts": [_concept_dict(c) for c in data["concepts"]],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


def export_csv(data: dict) -> bytes:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=_CSV_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for c in data["concepts"]:
        writer.writerow(_concept_row(c, data["products"]))
    return buf.getvalue().encode("utf-8")


def _render_brief(c: AdConcept, product: Optional[Product]) -> str:
    """Human-readable .txt brief for one concept variant."""
    scripts = c.script_json or {}
    captions = c.captions_json or {}
    compliance = c.compliance_json or {}
    lines = [
        f"PRODUCT: {product.title if product else c.product_id}",
        f"VARIANT: {c.variant_index + 1}    STATUS: {c.status}    "
        f"COMPLIANCE RISK: {compliance.get('risk', 'n/a')}",
        f"ANGLE: {c.angle or ''}",
        "",
        f"HOOK: {c.hook or ''}",
        f"HEADLINE: {c.headline or ''}",
        f"CTA: {c.cta or ''}",
        f"ON-SCREEN TEXT: {c.on_screen_text or ''}",
        "",
        "── SCRIPTS ──",
    ]
    for style in ("ugc", "demo", "influencer"):
        if scripts.get(style):
            lines += [f"[{style.upper()}]", scripts[style], ""]

    if captions:
        lines.append("── CAPTIONS ──")
        for platform, cap in captions.items():
            cap = cap or {}
            tags = " ".join(cap.get("hashtags", []) or [])
            lines += [f"[{platform}]", cap.get("caption", ""), tags, ""]

    scenes = (c.storyboard_json or {}).get("scenes") or []
    if scenes:
        lines.append("── STORYBOARD ──")
        for s in scenes:
            lines.append(
                f"  {s.get('scene_number')}. ({s.get('asset_type')}) "
                f"{s.get('narration', '')} | visual: {s.get('visual_prompt', '')} "
                f"| text: {s.get('on_screen_text', '')}"
            )

    flags = compliance.get("flags") or []
    if flags:
        lines += ["", "── COMPLIANCE FLAGS ──"]
        for f in flags:
            lines.append(f"  ! {f.get('text', '')} — {f.get('reason', '')} "
                         f"(fix: {f.get('suggested_fix', '')})")

    return "\n".join(lines)


def export_zip(data: dict) -> bytes:
    campaign = data["campaign"]
    products = data["products"]
    root = f"campaign_{_slug(campaign.name, campaign.id)}"

    # Pre-compute unique folder slugs per product to avoid collisions.
    used: dict[str, int] = {}
    folder_for: dict[str, str] = {}
    for pid, p in products.items():
        base = _slug(p.title, pid[:8])
        n = used.get(base, 0)
        used[base] = n + 1
        folder_for[pid] = base if n == 0 else f"{base}-{n + 1}"

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{root}/campaign.json", export_json(data))
        zf.writestr(f"{root}/all_concepts.csv", export_csv(data))
        for c in data["concepts"]:
            folder = folder_for.get(c.product_id, _slug(c.product_id[:8]))
            fname = f"{root}/{folder}/variant_{c.variant_index + 1}.txt"
            zf.writestr(fname, _render_brief(c, products.get(c.product_id)))
    return buf.getvalue()


# Format → (builder, media_type, file extension)
EXPORTERS = {
    "json": (export_json, "application/json", "json"),
    "csv": (export_csv, "text/csv", "csv"),
    "zip": (export_zip, "application/zip", "zip"),
}


def build_export(db: Session, campaign: Campaign, fmt: str) -> tuple[bytes, str, str]:
    """Return (content_bytes, media_type, filename) for the requested format."""
    if fmt not in EXPORTERS:
        raise ValueError(f"Unsupported export format: {fmt!r}. Use one of {sorted(EXPORTERS)}.")
    builder, media_type, ext = EXPORTERS[fmt]
    data = gather(db, campaign)
    content = builder(data)
    filename = f"campaign_{_slug(campaign.name, campaign.id)}.{ext}"
    return content, media_type, filename
