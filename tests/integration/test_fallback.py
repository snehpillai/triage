"""Integration tests for the cross-provider fallback in BaseSpecialist.

These tests exercise the full fallback code path inside the agentic loop:
  Anthropic raises a server error -> warning is logged -> OpenAI is called ->
  draft_response is populated -> provider == "openai_fallback"

No real API calls are made. Both the Anthropic and OpenAI clients are patched.
The retriever is patched to return an empty list (no DB required).
"""

from unittest.mock import MagicMock, patch

import anthropic
import pytest
from loguru import logger

from triage.agents.specialists.refund import RefundSpecialist

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SPECIALIST = RefundSpecialist()

_BASE_STATE: dict = {
    "ticket_id": "T-fallback-001",
    "content": "I want a refund for order ORD-9999.",
    "intent": "refund",
    "confidence": 0.95,
    "context_docs": [],
    "tool_results": {},
    "retry_count": 0,
    "qc_feedback": "",
}

_OAI_REPLY = (
    "Based on our 30-day return policy, order ORD-9999 is eligible for a full refund. "
    "Please initiate the return through your account portal."
)


def _make_503_error() -> anthropic.APIStatusError:
    """Build an APIStatusError with status_code=503."""
    mock_response = MagicMock()
    mock_response.status_code = 503
    return anthropic.APIStatusError("Service Unavailable", response=mock_response, body=None)


def _make_oai_response(text: str = _OAI_REPLY) -> MagicMock:
    """Build a minimal mock OpenAI chat completion response."""
    choice = MagicMock()
    choice.finish_reason = "stop"
    choice.message.content = text
    choice.message.tool_calls = None
    resp = MagicMock()
    resp.choices = [choice]
    return resp


# ---------------------------------------------------------------------------
# 503 fallback - happy path
# ---------------------------------------------------------------------------


class TestFallback503:
    def test_response_non_empty_after_503(self) -> None:
        with (
            patch("triage.agents.specialists.base._client") as mock_anthropic,
            patch("triage.agents.specialists.base._oai_client") as mock_oai,
            patch("triage.agents.specialists.base.retrieve", return_value=[]),
        ):
            mock_anthropic.messages.create.side_effect = _make_503_error()
            mock_oai.chat.completions.create.return_value = _make_oai_response()
            result = _SPECIALIST.run(_BASE_STATE)

        assert result.get("draft_response"), "draft_response must be non-empty"
        assert len(result["draft_response"]) >= 10

    def test_provider_is_openai_fallback_after_503(self) -> None:
        with (
            patch("triage.agents.specialists.base._client") as mock_anthropic,
            patch("triage.agents.specialists.base._oai_client") as mock_oai,
            patch("triage.agents.specialists.base.retrieve", return_value=[]),
        ):
            mock_anthropic.messages.create.side_effect = _make_503_error()
            mock_oai.chat.completions.create.return_value = _make_oai_response()
            result = _SPECIALIST.run(_BASE_STATE)

        assert result["provider"] == "openai_fallback"

    def test_warning_logged_on_503(self) -> None:
        captured: list[str] = []
        sink_id = logger.add(lambda msg: captured.append(str(msg)), level="WARNING")
        try:
            with (
                patch("triage.agents.specialists.base._client") as mock_anthropic,
                patch("triage.agents.specialists.base._oai_client") as mock_oai,
                patch("triage.agents.specialists.base.retrieve", return_value=[]),
            ):
                mock_anthropic.messages.create.side_effect = _make_503_error()
                mock_oai.chat.completions.create.return_value = _make_oai_response()
                _SPECIALIST.run(_BASE_STATE)
        finally:
            logger.remove(sink_id)

        assert captured, "expected at least one WARNING log entry"
        combined = " ".join(captured)
        assert "503" in combined or "fallback" in combined.lower()

    def test_openai_is_called_exactly_once_for_simple_response(self) -> None:
        """No tool calls in the OpenAI response -> exactly one OAI call."""
        with (
            patch("triage.agents.specialists.base._client") as mock_anthropic,
            patch("triage.agents.specialists.base._oai_client") as mock_oai,
            patch("triage.agents.specialists.base.retrieve", return_value=[]),
        ):
            mock_anthropic.messages.create.side_effect = _make_503_error()
            mock_oai.chat.completions.create.return_value = _make_oai_response()
            _SPECIALIST.run(_BASE_STATE)

        mock_oai.chat.completions.create.assert_called_once()

    def test_anthropic_not_called_again_after_fallback(self) -> None:
        """Once we switch to OpenAI, Anthropic is never retried."""
        with (
            patch("triage.agents.specialists.base._client") as mock_anthropic,
            patch("triage.agents.specialists.base._oai_client") as mock_oai,
            patch("triage.agents.specialists.base.retrieve", return_value=[]),
        ):
            mock_anthropic.messages.create.side_effect = _make_503_error()
            mock_oai.chat.completions.create.return_value = _make_oai_response()
            _SPECIALIST.run(_BASE_STATE)

        # Anthropic was called once (and failed), not retried.
        mock_anthropic.messages.create.assert_called_once()


# ---------------------------------------------------------------------------
# Timeout fallback
# ---------------------------------------------------------------------------


class TestFallbackTimeout:
    def test_timeout_triggers_openai_fallback(self) -> None:
        with (
            patch("triage.agents.specialists.base._client") as mock_anthropic,
            patch("triage.agents.specialists.base._oai_client") as mock_oai,
            patch("triage.agents.specialists.base.retrieve", return_value=[]),
        ):
            mock_anthropic.messages.create.side_effect = anthropic.APITimeoutError(
                request=MagicMock()
            )
            mock_oai.chat.completions.create.return_value = _make_oai_response()
            result = _SPECIALIST.run(_BASE_STATE)

        assert result["provider"] == "openai_fallback"
        assert result.get("draft_response")


# ---------------------------------------------------------------------------
# Happy path (Anthropic works) - provider should be "anthropic"
# ---------------------------------------------------------------------------


class TestProviderTracking:
    def test_provider_is_anthropic_on_normal_success(self) -> None:
        """When Anthropic succeeds, provider must be 'anthropic'."""
        anthr_resp = MagicMock()
        anthr_resp.stop_reason = "end_turn"
        anthr_resp.usage.output_tokens = 80
        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = (
            "Per our 30-day return policy, your refund for ORD-9999 has been approved."
        )
        anthr_resp.content = [text_block]

        with (
            patch("triage.agents.specialists.base._client") as mock_anthropic,
            patch("triage.agents.specialists.base.retrieve", return_value=[]),
        ):
            mock_anthropic.messages.create.return_value = anthr_resp
            result = _SPECIALIST.run(_BASE_STATE)

        assert result["provider"] == "anthropic"

    def test_non_server_error_is_not_swallowed(self) -> None:
        """A 400 Bad Request must propagate, not trigger fallback."""
        mock_response = MagicMock()
        mock_response.status_code = 400
        bad_request = anthropic.APIStatusError("Bad Request", response=mock_response, body=None)

        with (
            patch("triage.agents.specialists.base._client") as mock_anthropic,
            patch("triage.agents.specialists.base.retrieve", return_value=[]),
        ):
            mock_anthropic.messages.create.side_effect = bad_request
            with pytest.raises(anthropic.APIStatusError):
                _SPECIALIST.run(_BASE_STATE)


# ---------------------------------------------------------------------------
# Message conversion
# ---------------------------------------------------------------------------


class TestToOaiMessages:
    """Unit tests for the _to_oai_messages converter (no mocking needed)."""

    def test_string_user_message_passes_through(self) -> None:
        from triage.agents.specialists.base import _to_oai_messages

        result = _to_oai_messages([{"role": "user", "content": "hello"}])
        assert result == [{"role": "user", "content": "hello"}]

    def test_tool_result_becomes_tool_role(self) -> None:
        from triage.agents.specialists.base import _to_oai_messages

        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "call_abc",
                        "content": '{"found": true}',
                    }
                ],
            }
        ]
        result = _to_oai_messages(messages)
        assert result == [
            {"role": "tool", "tool_call_id": "call_abc", "content": '{"found": true}'}
        ]

    def test_assistant_tool_use_block_converted(self) -> None:
        from triage.agents.specialists.base import _to_oai_messages

        tool_block = MagicMock()
        tool_block.type = "tool_use"
        tool_block.id = "call_xyz"
        tool_block.name = "order_lookup"
        tool_block.input = {"order_id": "ORD-1"}

        messages = [{"role": "assistant", "content": [tool_block]}]
        result = _to_oai_messages(messages)

        assert len(result) == 1
        assert result[0]["role"] == "assistant"
        assert result[0]["tool_calls"][0]["id"] == "call_xyz"
        assert result[0]["tool_calls"][0]["function"]["name"] == "order_lookup"

    def test_assistant_text_block_converted(self) -> None:
        from triage.agents.specialists.base import _to_oai_messages

        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = "Let me look that up."

        messages = [{"role": "assistant", "content": [text_block]}]
        result = _to_oai_messages(messages)

        assert result[0]["content"] == "Let me look that up."
