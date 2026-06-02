"""Usage tracking for provider API calls.

UsageCollector is a lightweight in-memory buffer. Create one per pipeline run,
pass it into provider-wrapper functions (summarize, tts, image_gen, analysis),
then call flush() at the end of the Celery task to write all events to the DB.

This design keeps DB sessions out of provider modules and avoids partial writes
if a task fails mid-way — all events are committed in one shot when the task
completes successfully (or on a best-effort flush in the finally block).
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import select, func

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
    from app.models import GenerationUsageEvent

logger = logging.getLogger(__name__)


class UsageCollector:
    """Buffer that accumulates provider usage events during a generation run.

    Usage:
        collector = UsageCollector(article_id=article.id, user_id=user_id)
        # pass collector to provider functions …
        collector.flush(db, video_asset_id=video_record.id)
    """

    def __init__(
        self,
        article_id: str | None = None,
        user_id: str | None = None,
        *,
        workspace_id: str | None = None,
        product_id: str | None = None,
        ad_concept_id: str | None = None,
    ) -> None:
        self.article_id = article_id
        self.user_id = user_id
        # Catalog-to-ad linkage so cost can be billed per workspace/product.
        self.workspace_id = workspace_id
        self.product_id = product_id
        self.ad_concept_id = ad_concept_id
        self._events: list[dict] = []

    def record(self, **kwargs) -> None:
        """Buffer a single usage event dict."""
        evt: dict = {
            "article_id": self.article_id,
            "user_id": self.user_id,
            "workspace_id": self.workspace_id,
            "product_id": self.product_id,
            "ad_concept_id": self.ad_concept_id,
        }
        evt.update(kwargs)
        self._events.append(evt)

    def flush(self, db: "Session", video_asset_id: str | None = None) -> int:
        """Write buffered events to the database.

        Returns the number of rows written. Clears the buffer on success.
        Never raises — logs errors and returns 0 on failure so generation
        tasks are never blocked by tracking failures.
        """
        if not self._events:
            return 0

        from app.models import GenerationUsageEvent

        try:
            rows = []
            for evt in self._events:
                row = GenerationUsageEvent(
                    article_id=evt.get("article_id"),
                    user_id=evt.get("user_id"),
                    workspace_id=evt.get("workspace_id"),
                    product_id=evt.get("product_id"),
                    ad_concept_id=evt.get("ad_concept_id"),
                    video_asset_id=video_asset_id or evt.get("video_asset_id"),
                    provider=evt["provider"],
                    operation=evt["operation"],
                    model=evt.get("model"),
                    external_request_id=evt.get("external_request_id"),
                    input_tokens=evt.get("input_tokens"),
                    output_tokens=evt.get("output_tokens"),
                    total_tokens=evt.get("total_tokens"),
                    cached_input_tokens=evt.get("cached_input_tokens"),
                    image_count=evt.get("image_count"),
                    image_size=evt.get("image_size"),
                    image_quality=evt.get("image_quality"),
                    character_count=evt.get("character_count"),
                    estimated_cost_usd=evt.get("estimated_cost_usd"),
                    pricing_snapshot=evt.get("pricing_snapshot"),
                    metadata_json=evt.get("metadata"),
                )
                db.add(row)
                rows.append(row)
            db.commit()
            self._events.clear()
            logger.debug("Flushed %d usage events for article=%s", len(rows), self.article_id)
            return len(rows)
        except Exception as exc:
            logger.warning("Failed to flush usage events (non-fatal): %s", exc)
            db.rollback()
            return 0


# ── Rollup queries ─────────────────────────────────────────────────────────

def get_article_cost_summary(db: "Session", article_id: str) -> dict:
    """Aggregate all usage events for an article into a cost summary dict."""
    from app.models import GenerationUsageEvent
    from app.pricing import PRICING_NOTE

    events = db.execute(
        select(GenerationUsageEvent)
        .where(GenerationUsageEvent.article_id == article_id)
        .order_by(GenerationUsageEvent.created_at)
    ).scalars().all()

    total = 0.0
    by_provider: dict[str, float] = {}
    by_stage: dict[str, float] = {}
    total_tokens = 0
    total_chars = 0

    for e in events:
        cost = e.estimated_cost_usd or 0.0
        total += cost
        by_provider[e.provider] = by_provider.get(e.provider, 0.0) + cost
        by_stage[e.operation] = by_stage.get(e.operation, 0.0) + cost
        total_tokens += (e.total_tokens or 0)
        total_chars += (e.character_count or 0)

    return {
        "article_id": article_id,
        "total_estimated_usd": round(total, 6),
        "by_provider": {k: round(v, 6) for k, v in by_provider.items()},
        "by_stage": {k: round(v, 6) for k, v in by_stage.items()},
        "total_tokens": total_tokens,
        "total_characters": total_chars,
        "event_count": len(events),
        "events": [_event_to_dict(e) for e in events],
        "pricing_note": PRICING_NOTE,
    }


def _event_to_dict(e: "GenerationUsageEvent") -> dict:
    return {
        "id": e.id,
        "provider": e.provider,
        "operation": e.operation,
        "model": e.model,
        "input_tokens": e.input_tokens,
        "output_tokens": e.output_tokens,
        "total_tokens": e.total_tokens,
        "cached_input_tokens": e.cached_input_tokens,
        "character_count": e.character_count,
        "image_count": e.image_count,
        "image_size": e.image_size,
        "image_quality": e.image_quality,
        "estimated_cost_usd": e.estimated_cost_usd,
        "external_request_id": e.external_request_id,
        "created_at": e.created_at.isoformat() if e.created_at else None,
    }
