"""Unit tests for the circuit breaker state machine.

Uses an in-process FakeRedis that implements the four operations the
circuit breaker needs (get, incr, set, delete). No running Redis required.
"""

import time
from unittest.mock import MagicMock

import pytest

from triage.tools.circuit_breaker import (
    _FAILURE_THRESHOLD,
    CircuitBreaker,
    CircuitOpenError,
    open_message,
)

# ---------------------------------------------------------------------------
# Minimal in-process Redis stand-in
# ---------------------------------------------------------------------------


class FakeRedis:
    """In-memory implementation of the Redis subset used by CircuitBreaker."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self._store.get(key)

    def incr(self, key: str) -> int:
        val = int(self._store.get(key, "0")) + 1
        self._store[key] = str(val)
        return val

    def set(self, key: str, value: str) -> None:
        self._store[key] = str(value)

    def delete(self, *keys: str) -> int:
        removed = sum(1 for k in keys if k in self._store)
        for k in keys:
            self._store.pop(k, None)
        return removed


def _fresh_cb() -> CircuitBreaker:
    return CircuitBreaker(FakeRedis())


def _broken_tool(exc: Exception | None = None) -> MagicMock:
    return MagicMock(side_effect=exc or Exception("tool failed"))


# ---------------------------------------------------------------------------
# Core spec: 5 failures open the circuit, 6th short-circuits
# ---------------------------------------------------------------------------


class TestCircuitOpensAfterThreshold:
    def test_circuit_opens_after_5th_failure(self) -> None:
        cb = _fresh_cb()
        tool = _broken_tool()

        for _ in range(_FAILURE_THRESHOLD - 1):
            with pytest.raises(Exception, match="tool failed"):
                cb.call("my_tool", tool)

        # 5th call: tool is invoked AND circuit opens after the exception.
        with pytest.raises(Exception, match="tool failed"):
            cb.call("my_tool", tool)

        assert tool.call_count == _FAILURE_THRESHOLD

    def test_sixth_call_short_circuits_with_circuit_open_error(self) -> None:
        cb = _fresh_cb()
        tool = _broken_tool()

        for _ in range(_FAILURE_THRESHOLD):
            with pytest.raises(Exception, match="tool failed|b failed"):
                cb.call("my_tool", tool)

        # 6th call must raise CircuitOpenError - tool must NOT be called.
        with pytest.raises(CircuitOpenError) as exc_info:
            cb.call("my_tool", tool)

        assert exc_info.value.tool_name == "my_tool"
        assert (
            tool.call_count == _FAILURE_THRESHOLD
        ), "tool must not be called after circuit is open"

    def test_tool_not_called_on_open_circuit(self) -> None:
        cb = _fresh_cb()
        tool = _broken_tool()

        for _ in range(_FAILURE_THRESHOLD):
            with pytest.raises(Exception, match="tool failed|b failed"):
                cb.call("my_tool", tool)

        call_count_before = tool.call_count
        with pytest.raises(CircuitOpenError):
            cb.call("my_tool", tool)

        assert tool.call_count == call_count_before


# ---------------------------------------------------------------------------
# Stays closed below threshold
# ---------------------------------------------------------------------------


class TestCircuitStaysClosedBelowThreshold:
    def test_four_failures_do_not_open_circuit(self) -> None:
        cb = _fresh_cb()
        tool = _broken_tool()

        for _ in range(_FAILURE_THRESHOLD - 1):
            with pytest.raises(Exception, match="tool failed"):
                cb.call("my_tool", tool)

        # Should still be closed - next call raises original exception, not CircuitOpenError.
        with pytest.raises(Exception, match="tool failed"):
            cb.call("my_tool", tool)

    def test_independent_tools_do_not_share_state(self) -> None:
        cb = _fresh_cb()
        tool_a = _broken_tool()
        tool_b = _broken_tool(Exception("b failed"))

        for _ in range(_FAILURE_THRESHOLD):
            with pytest.raises(Exception, match="tool failed|b failed"):
                cb.call("tool_a", tool_a)

        # tool_a is open but tool_b should still be closed.
        with pytest.raises(CircuitOpenError):
            cb.call("tool_a", tool_a)

        with pytest.raises(Exception, match="b failed"):
            cb.call("tool_b", tool_b)


# ---------------------------------------------------------------------------
# Success resets counter
# ---------------------------------------------------------------------------


class TestSuccessResetsCounter:
    def test_success_clears_failure_count(self) -> None:
        cb = _fresh_cb()
        tool = _broken_tool()

        for _ in range(_FAILURE_THRESHOLD - 1):
            with pytest.raises(Exception, match="tool failed|b failed"):
                cb.call("my_tool", tool)

        # One success clears the counter.
        good_tool = MagicMock(return_value="ok")
        result = cb.call("my_tool", good_tool)
        assert result == "ok"

        # Now fail again - needs a full FAILURE_THRESHOLD of new failures.
        tool2 = _broken_tool(Exception("second failure"))
        for _ in range(_FAILURE_THRESHOLD - 1):
            with pytest.raises(Exception, match="second failure"):
                cb.call("my_tool", tool2)

        # Still closed - not enough new failures yet.
        with pytest.raises(Exception, match="second failure"):
            cb.call("my_tool", tool2)

    def test_success_clears_open_until(self) -> None:
        """A success after the timeout expires should fully reset the circuit."""
        cb = _fresh_cb()
        tool = _broken_tool()

        for _ in range(_FAILURE_THRESHOLD):
            with pytest.raises(Exception, match="tool failed"):
                cb.call("my_tool", tool)

        # Expire the circuit manually (failures key still holds 5).
        cb.redis.set("circuit:my_tool:open_until", str(time.time() - 1.0))

        # A success while expired clears both keys - failures never re-open the
        # circuit when the first post-expiry call succeeds.
        good_tool = MagicMock(return_value="ok")
        result = cb.call("my_tool", good_tool)
        assert result == "ok"
        assert cb.redis.get("circuit:my_tool:failures") is None
        assert cb.redis.get("circuit:my_tool:open_until") is None


# ---------------------------------------------------------------------------
# Circuit re-closes after timeout (half-open behaviour)
# ---------------------------------------------------------------------------


class TestCircuitExpiry:
    def test_expired_circuit_allows_new_attempts(self) -> None:
        cb = _fresh_cb()
        tool = _broken_tool()

        for _ in range(_FAILURE_THRESHOLD):
            with pytest.raises(Exception, match="tool failed|b failed"):
                cb.call("my_tool", tool)

        # Manually expire the open_until timestamp.
        cb.redis.set("circuit:my_tool:open_until", str(time.time() - 1.0))

        # Next call goes through (and fails with original exception, not CircuitOpenError).
        with pytest.raises(Exception, match="tool failed"):
            cb.call("my_tool", tool)


# ---------------------------------------------------------------------------
# Redis unavailability - fail open
# ---------------------------------------------------------------------------


class TestRedisFailOpen:
    def test_redis_error_on_check_fails_open(self) -> None:
        """If Redis is down during circuit check, tool call proceeds."""
        cb = _fresh_cb()
        cb.redis = MagicMock(side_effect=Exception("Redis down"))

        good_tool = MagicMock(return_value="result")

        # Redis error is swallowed; tool is called normally.
        # (The redis error on record_success is also swallowed.)
        result = cb.call("my_tool", good_tool)
        assert result == "result"


# ---------------------------------------------------------------------------
# open_message helper
# ---------------------------------------------------------------------------


class TestOpenMessage:
    def test_contains_tool_name(self) -> None:
        msg = open_message("order_lookup")
        assert "order_lookup" in msg

    def test_contains_human_readable_purpose(self) -> None:
        msg = open_message("order_lookup")
        assert "order lookup" in msg

    def test_account_status_tool_purpose(self) -> None:
        msg = open_message("account_status")
        assert "account status" in msg

    def test_unavailable_phrasing(self) -> None:
        msg = open_message("some_tool")
        assert "unavailable" in msg.lower()
        assert "Do not make claims" in msg


# ---------------------------------------------------------------------------
# CircuitOpenError attributes
# ---------------------------------------------------------------------------


class TestCircuitOpenError:
    def test_tool_name_attribute(self) -> None:
        err = CircuitOpenError("my_tool")
        assert err.tool_name == "my_tool"

    def test_str_representation(self) -> None:
        err = CircuitOpenError("my_tool")
        assert "my_tool" in str(err)
