"""Prometheus metrics for the pipeline.

One registry shared across the API and the Celery workers. Worker metrics
are exported via a separate endpoint or scraped through a sidecar — the
default MultiProcessCollector path keeps things simple for single-process
uvicorn + Celery prefork workers.

Call sites import the module-level metric objects and update them inline.
Keep metric cardinality low: label values should be bounded small sets
(provider, stage, platform) — never raw IDs or user-supplied strings.
"""
from __future__ import annotations

from typing import Any

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)


# ── HTTP layer ─────────────────────────────────────────────────────────────

http_requests_total = Counter(
    "http_requests_total",
    "HTTP requests processed by the API, labelled by method and status class.",
    labelnames=("method", "status_class"),
)

http_request_duration_seconds = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency in seconds.",
    labelnames=("method",),
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)


# ── Task pipeline stages ───────────────────────────────────────────────────

task_duration_seconds = Histogram(
    "task_duration_seconds",
    "End-to-end Celery task duration in seconds, labelled by task name.",
    labelnames=("task_name",),
    buckets=(5, 15, 30, 60, 120, 300, 600, 1200),
)

task_stage_failures_total = Counter(
    "task_stage_failures_total",
    "Failures at a named pipeline stage (extraction, script, storyboard, tts, imaging, video).",
    labelnames=("stage",),
)


# ── External provider calls ────────────────────────────────────────────────

llm_calls_total = Counter(
    "llm_calls_total",
    "LLM calls by provider/operation/outcome.",
    labelnames=("provider", "operation", "outcome"),
)

llm_input_tokens_total = Counter(
    "llm_input_tokens_total",
    "Cumulative LLM input tokens.",
    labelnames=("provider", "model"),
)

llm_output_tokens_total = Counter(
    "llm_output_tokens_total",
    "Cumulative LLM output tokens.",
    labelnames=("provider", "model"),
)

tts_calls_total = Counter(
    "tts_calls_total",
    "TTS calls by provider/outcome.",
    labelnames=("provider", "outcome"),
)

tts_characters_total = Counter(
    "tts_characters_total",
    "Cumulative TTS characters synthesised.",
    labelnames=("provider",),
)

image_gen_calls_total = Counter(
    "image_gen_calls_total",
    "Image generation calls by provider/outcome.",
    labelnames=("provider", "outcome"),
)

video_assembly_duration_seconds = Histogram(
    "video_assembly_duration_seconds",
    "FFmpeg video assembly duration in seconds.",
    labelnames=("render_mode",),
    buckets=(5, 15, 30, 60, 120, 300, 600),
)


# ── Queue health ───────────────────────────────────────────────────────────

celery_queue_depth = Gauge(
    "celery_queue_depth",
    "Approximate pending tasks per Celery queue (scraped lazily).",
    labelnames=("queue",),
)

active_tasks_per_user = Gauge(
    "active_tasks_per_user",
    "Current active tasks counter per user (aggregated).",
)


# ── Scrape helpers ─────────────────────────────────────────────────────────

def render_metrics() -> tuple[bytes, str]:
    """Return (body, content_type) for an HTTP /metrics response."""
    return generate_latest(), CONTENT_TYPE_LATEST


def observe_llm_usage(
    provider: str,
    model: str,
    input_tokens: int | None,
    output_tokens: int | None,
) -> None:
    if input_tokens:
        llm_input_tokens_total.labels(provider=provider, model=model).inc(input_tokens)
    if output_tokens:
        llm_output_tokens_total.labels(provider=provider, model=model).inc(output_tokens)
