"""Sentry initialization.

Gated on the ``SENTRY_DSN`` environment variable — a no-op when the DSN
is empty so local development and CI don't need a Sentry account.
Call ``init_sentry()`` once per process (API and each Celery worker).
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_initialized = False


def init_sentry() -> bool:
    """Initialize Sentry if SENTRY_DSN is configured. Safe to call multiple times."""
    global _initialized
    if _initialized:
        return True

    dsn = os.getenv("SENTRY_DSN", "").strip()
    if not dsn:
        return False

    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.celery import CeleryIntegration
        from sentry_sdk.integrations.logging import LoggingIntegration
    except ImportError:
        logger.warning("SENTRY_DSN set but sentry-sdk is not installed")
        return False

    environment = os.getenv("SENTRY_ENVIRONMENT", os.getenv("APP_ENV", "production"))
    release = os.getenv("SENTRY_RELEASE") or None
    traces_sample_rate = float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.1"))
    profiles_sample_rate = float(os.getenv("SENTRY_PROFILES_SAMPLE_RATE", "0.0"))

    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        release=release,
        traces_sample_rate=traces_sample_rate,
        profiles_sample_rate=profiles_sample_rate,
        send_default_pii=False,
        integrations=[
            FastApiIntegration(transaction_style="endpoint"),
            CeleryIntegration(propagate_traces=True),
            LoggingIntegration(
                level=logging.INFO,        # breadcrumbs at INFO+
                event_level=logging.ERROR, # events at ERROR+
            ),
        ],
    )
    logger.info("Sentry initialized (env=%s)", environment)
    _initialized = True
    return True
