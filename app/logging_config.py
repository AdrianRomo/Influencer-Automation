"""JSON logging + correlation-ID propagation.

One-line-per-event JSON logs so Loki/CloudWatch/Datadog can parse cleanly.
Correlation IDs are stored in a contextvar and injected into every log
record; FastAPI middleware assigns one per request and Celery tasks pull
it from their kwargs so a single article's journey can be traced across
HTTP → queue → worker.

Enable JSON output by setting LOG_FORMAT=json (default is human-readable).
"""
from __future__ import annotations

import json
import logging
import os
import sys
import uuid
from contextvars import ContextVar
from typing import Any, Optional


# Context variables propagated into every log record. These are process-local
# but async/thread-safe via contextvars — each request/task gets its own view.
_correlation_id: ContextVar[str] = ContextVar("correlation_id", default="")
_user_id: ContextVar[str] = ContextVar("user_id", default="")
_article_id: ContextVar[str] = ContextVar("article_id", default="")
_task_id: ContextVar[str] = ContextVar("task_id", default="")


def get_correlation_id() -> str:
    return _correlation_id.get()


def set_correlation_id(cid: Optional[str] = None) -> str:
    """Set the current correlation id and return it."""
    cid = cid or uuid.uuid4().hex[:16]
    _correlation_id.set(cid)
    return cid


def set_log_context(
    *,
    user_id: Optional[str] = None,
    article_id: Optional[str] = None,
    task_id: Optional[str] = None,
) -> None:
    """Attach extra context vars to every log record from this point forward."""
    if user_id is not None:
        _user_id.set(user_id)
    if article_id is not None:
        _article_id.set(article_id)
    if task_id is not None:
        _task_id.set(task_id)


def clear_log_context() -> None:
    _correlation_id.set("")
    _user_id.set("")
    _article_id.set("")
    _task_id.set("")


class _ContextFilter(logging.Filter):
    """Inject contextvars into every LogRecord."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = _correlation_id.get()
        record.user_id = _user_id.get()
        record.article_id = _article_id.get()
        record.task_id = _task_id.get()
        return True


class JsonFormatter(logging.Formatter):
    """Minimal JSON formatter — one event per line, no external deps."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for attr in ("correlation_id", "user_id", "article_id", "task_id"):
            value = getattr(record, attr, "")
            if value:
                payload[attr] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        # Any extras passed via logger.info("...", extra={...}) land as attributes
        # on the record — copy the simple ones through.
        for key, value in record.__dict__.items():
            if key in payload or key.startswith("_"):
                continue
            if key in {
                "name", "msg", "args", "levelname", "levelno", "pathname",
                "filename", "module", "exc_info", "exc_text", "stack_info",
                "lineno", "funcName", "created", "msecs", "relativeCreated",
                "thread", "threadName", "processName", "process", "message",
                "taskName", "correlation_id", "user_id", "article_id", "task_id",
            }:
                continue
            try:
                json.dumps(value)
                payload[key] = value
            except (TypeError, ValueError):
                payload[key] = repr(value)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: Optional[str] = None) -> None:
    """Install the root logger, formatter, and context filter.

    Idempotent — safe to call multiple times from both the API process and
    each Celery worker.
    """
    log_level = (level or os.getenv("LOG_LEVEL", "INFO")).upper()
    use_json = os.getenv("LOG_FORMAT", "text").lower() == "json"

    root = logging.getLogger()
    # Remove any handlers added by libraries (uvicorn etc.) so ours is authoritative.
    for h in list(root.handlers):
        root.removeHandler(h)

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.addFilter(_ContextFilter())
    if use_json:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] %(name)s [%(correlation_id)s] %(message)s",
            datefmt="%H:%M:%S",
        ))
    root.addHandler(handler)
    root.setLevel(log_level)

    # Quiet down noisy library loggers
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
