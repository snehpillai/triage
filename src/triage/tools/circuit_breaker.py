"""Per-tool circuit breaker backed by Redis.

States
------
CLOSED  failures < threshold, tool calls pass through normally.
OPEN    failures >= threshold, open_until is set and in the future.
        Calls raise CircuitOpenError before touching the tool.

The circuit re-enters CLOSED automatically once open_until expires
(next successful call clears both keys; failed calls while half-open
restart the failure counter).

Redis keys (per tool_name)
--------------------------
circuit:{tool_name}:failures   - integer failure counter
circuit:{tool_name}:open_until - Unix timestamp (string) until which circuit is OPEN
"""

import time
from collections.abc import Callable
from typing import Any, TypeVar

import redis as redis_lib
from loguru import logger

from triage.config import settings

_FAILURE_THRESHOLD = 5
_OPEN_DURATION_SECONDS = 60

T = TypeVar("T")


class CircuitOpenError(Exception):
    """Raised when a tool's circuit breaker is open (service unavailable)."""

    def __init__(self, tool_name: str) -> None:
        self.tool_name = tool_name
        super().__init__(f"Circuit breaker open for tool '{tool_name}'")


def open_message(tool_name: str) -> str:
    """Tool-result content injected into the conversation when circuit is open.

    Tells the model not to fabricate data for the unavailable service.
    """
    purpose = tool_name.replace("_", " ")
    return (
        f"The {tool_name} service is currently unavailable. "
        f"Do not make claims about specific {purpose} details."
    )


class CircuitBreaker:
    """Circuit breaker state machine.

    Pass a Redis client (or any object with get/incr/set/delete) at
    construction time so tests can inject a fake.
    """

    def __init__(self, redis_client: Any) -> None:
        self.redis = redis_client

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def call(self, tool_name: str, fn: Callable[[], T]) -> T:
        """Execute fn with circuit breaker protection.

        Raises CircuitOpenError (without calling fn) if the circuit is open.
        Re-raises any exception from fn after recording the failure.
        """
        self._check(tool_name)

        try:
            result = fn()
        except Exception:
            self._record_failure(tool_name)
            raise

        self._record_success(tool_name)
        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _check(self, tool_name: str) -> None:
        """Raise CircuitOpenError if open_until is in the future."""
        try:
            raw = self.redis.get(f"circuit:{tool_name}:open_until")
        except Exception as exc:
            logger.warning(
                "CircuitBreaker: Redis unavailable on check for tool={t}, failing open: {e}",
                t=tool_name,
                e=exc,
            )
            return  # fail open

        if raw is not None:
            open_until = float(raw)
            if time.time() < open_until:
                raise CircuitOpenError(tool_name)

    def _record_failure(self, tool_name: str) -> None:
        """Increment failure counter; open the circuit if threshold is reached."""
        try:
            failures = self.redis.incr(f"circuit:{tool_name}:failures")
            if failures >= _FAILURE_THRESHOLD:
                open_until = time.time() + _OPEN_DURATION_SECONDS
                self.redis.set(f"circuit:{tool_name}:open_until", str(open_until))
                logger.warning(
                    "CircuitBreaker: OPEN for tool={t} after {n} failures "
                    "(closed again at {ts:.0f})",
                    t=tool_name,
                    n=failures,
                    ts=open_until,
                )
        except Exception as exc:
            logger.warning(
                "CircuitBreaker: Redis unavailable on failure record for tool={t}: {e}",
                t=tool_name,
                e=exc,
            )

    def _record_success(self, tool_name: str) -> None:
        """Reset both keys so the circuit returns to CLOSED."""
        try:
            self.redis.delete(
                f"circuit:{tool_name}:failures",
                f"circuit:{tool_name}:open_until",
            )
        except Exception as exc:
            logger.warning(
                "CircuitBreaker: Redis unavailable on success reset for tool={t}: {e}",
                t=tool_name,
                e=exc,
            )


# ---------------------------------------------------------------------------
# Module-level instance used by all specialists.
# Redis connection is lazy - import succeeds even when Redis is not running.
# ---------------------------------------------------------------------------
_redis = redis_lib.from_url(settings.redis_url, decode_responses=True)
circuit_breaker = CircuitBreaker(_redis)
