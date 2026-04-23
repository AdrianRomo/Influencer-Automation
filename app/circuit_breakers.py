"""Circuit breakers for external provider API calls.

When a provider sustains failures, the breaker opens and raises
CircuitBreakerError instead of calling the provider again, giving it time
to recover.  State resets automatically after reset_timeout seconds.
Env vars: CIRCUIT_BREAKER_FAIL_MAX (default 5), CIRCUIT_BREAKER_RESET_TIMEOUT (default 60).
"""
from __future__ import annotations

import logging
import os

import pybreaker

logger = logging.getLogger(__name__)


class _StateLogger(pybreaker.CircuitBreakerListener):
    def state_change(self, cb: pybreaker.CircuitBreaker, old_state, new_state) -> None:
        logger.warning("Circuit breaker %r: %s → %s", cb.name, old_state.name, new_state.name)


_listener = _StateLogger()
_fail_max = int(os.getenv("CIRCUIT_BREAKER_FAIL_MAX", "5"))
_reset_timeout = int(os.getenv("CIRCUIT_BREAKER_RESET_TIMEOUT", "60"))

openai_breaker = pybreaker.CircuitBreaker(
    fail_max=_fail_max,
    reset_timeout=_reset_timeout,
    name="openai",
    listeners=[_listener],
)

elevenlabs_breaker = pybreaker.CircuitBreaker(
    fail_max=_fail_max,
    reset_timeout=_reset_timeout,
    name="elevenlabs",
    listeners=[_listener],
)
