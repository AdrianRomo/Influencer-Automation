"""Health, metrics, and admin introspection routes."""
from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Depends, Response
from fastapi.responses import JSONResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import API_KEY, MAX_CONCURRENT_TASKS, check_api_key
from app.redis_client import get_redis

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/health")
def health(db: Session = Depends(get_db)):
    """Returns service health including DB and Redis reachability."""
    status = "ok"
    db_status = "ok"
    redis_status = "ok"

    try:
        db.execute(func.now())
    except Exception as exc:
        logger.error("Health check DB error: %s", exc)
        db_status = "error"
        status = "degraded"

    try:
        get_redis().ping()
    except Exception as exc:
        logger.error("Health check Redis error: %s", exc)
        redis_status = "error"
        status = "degraded"

    return JSONResponse(
        status_code=200 if status == "ok" else 503,
        content={"status": status, "db": db_status, "redis": redis_status},
    )


@router.get("/metrics")
def metrics():
    """Prometheus scrape endpoint; also refreshes celery queue depth."""
    from app.metrics import celery_queue_depth, render_metrics
    try:
        depth = get_redis().llen("celery")
        celery_queue_depth.labels(queue="celery").set(int(depth or 0))
    except Exception:
        pass
    body, content_type = render_metrics()
    return Response(content=body, media_type=content_type)


@router.get("/admin/beat-info", dependencies=[Depends(check_api_key)])
def beat_info():
    """Return the current scheduled generation configuration."""
    enabled = os.getenv("ENABLE_SCHEDULED_GENERATION", "false").lower() == "true"
    interval_h = int(os.getenv("BEAT_GENERATION_INTERVAL_HOURS", "0"))
    return {
        "enabled": enabled,
        "schedule": (
            f"every {interval_h}h" if interval_h > 0
            else f"daily at {os.getenv('BEAT_GENERATION_HOUR', '6')}:00 UTC"
        ),
        "skip_recent_hours": int(os.getenv("BEAT_SKIP_RECENT_HOURS", "4")),
        "max_concurrent_tasks_per_user": MAX_CONCURRENT_TASKS,
    }
