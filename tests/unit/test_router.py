"""Unit tests for the router agent. No real API calls are made."""

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import HumanMessage

from triage.agents.router import route

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_response(intent: str, confidence: float, reasoning: str = "test reasoning") -> MagicMock:
    """Build a minimal mock Anthropic response containing one tool_use block."""
    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.input = {"intent": intent, "confidence": confidence, "reasoning": reasoning}

    response = MagicMock()
    response.content = [tool_block]
    response.usage.input_tokens = 45
    response.usage.output_tokens = 18
    return response


def _state(content: str, ticket_id: str = "t-test") -> dict[str, Any]:
    """Minimal TicketState dict for routing tests."""
    return {"ticket_id": ticket_id, "content": content}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_client():
    """Replace the module-level Anthropic client in router.py with a MagicMock.

    Patches triage.agents.router._client directly so no real API calls are
    made and the mock survives import-time client initialisation.
    """
    with patch("triage.agents.router._client") as client:
        yield client


# ---------------------------------------------------------------------------
# Classification tests (one per intent + ambiguous case)
# ---------------------------------------------------------------------------


class TestRouterClassification:
    def test_refund_ticket(self, mock_client: MagicMock) -> None:
        mock_client.messages.create.return_value = _make_response("refund", 0.95)
        result = route(_state("I want a full refund on my damaged laptop."))
        assert result["intent"] == "refund"
        assert result["confidence"] >= 0.7

    def test_technical_ticket(self, mock_client: MagicMock) -> None:
        mock_client.messages.create.return_value = _make_response("technical", 0.95)
        result = route(_state("I keep getting ERR-503 every time I try to log in."))
        assert result["intent"] == "technical"
        assert result["confidence"] >= 0.7

    def test_billing_ticket(self, mock_client: MagicMock) -> None:
        mock_client.messages.create.return_value = _make_response("billing", 0.95)
        result = route(_state("You charged me twice this month."))
        assert result["intent"] == "billing"
        assert result["confidence"] >= 0.7

    def test_account_ticket(self, mock_client: MagicMock) -> None:
        mock_client.messages.create.return_value = _make_response("account", 0.92)
        result = route(_state("I forgot my password and my 2FA phone was stolen."))
        assert result["intent"] == "account"
        assert result["confidence"] >= 0.7

    def test_ambiguous_ticket_returns_valid_intent_with_low_confidence(
        self, mock_client: MagicMock
    ) -> None:
        mock_client.messages.create.return_value = _make_response(
            "refund", 0.55, reasoning="Could be refund or billing"
        )
        result = route(_state("I have a problem with my purchase."))
        assert result["intent"] in ("refund", "technical", "billing", "account")
        assert result["confidence"] < 0.7


# ---------------------------------------------------------------------------
# Return-shape tests: verify structural guarantees regardless of intent
# ---------------------------------------------------------------------------


class TestRouterReturnShape:
    def test_all_required_keys_present(self, mock_client: MagicMock) -> None:
        mock_client.messages.create.return_value = _make_response("refund", 0.90)
        result = route(_state("Refund please."))
        for key in (
            "intent",
            "confidence",
            "retry_count",
            "escalate",
            "escalation_reason",
            "messages",
        ):
            assert key in result, f"missing key: {key}"

    def test_control_flow_fields_initialised_to_defaults(self, mock_client: MagicMock) -> None:
        mock_client.messages.create.return_value = _make_response("billing", 0.88)
        result = route(_state("Billing issue."))
        assert result["retry_count"] == 0
        assert result["escalate"] is False
        assert result["escalation_reason"] == ""

    def test_human_message_added_with_correct_content(self, mock_client: MagicMock) -> None:
        content = "My order never arrived and I want my money back."
        mock_client.messages.create.return_value = _make_response("refund", 0.91)
        result = route(_state(content))
        assert len(result["messages"]) == 1
        assert isinstance(result["messages"][0], HumanMessage)
        assert result["messages"][0].content == content


# ---------------------------------------------------------------------------
# API call contract: verify the router talks to Claude the right way
# ---------------------------------------------------------------------------


class TestRouterApiContract:
    def test_uses_configured_haiku_model(self, mock_client: MagicMock) -> None:
        mock_client.messages.create.return_value = _make_response("technical", 0.93)
        route(_state("Login error."))
        kwargs = mock_client.messages.create.call_args.kwargs
        assert kwargs["model"] == "claude-haiku-4-5-20251001"

    def test_forces_tool_choice(self, mock_client: MagicMock) -> None:
        """tool_choice must force the specific tool, not just suggest it."""
        mock_client.messages.create.return_value = _make_response("account", 0.90)
        route(_state("Password reset."))
        kwargs = mock_client.messages.create.call_args.kwargs
        assert kwargs["tool_choice"] == {"type": "tool", "name": "classify_ticket"}

    def test_ticket_content_sent_as_user_message(self, mock_client: MagicMock) -> None:
        content = "Specific ticket text that must reach the model."
        mock_client.messages.create.return_value = _make_response("refund", 0.88)
        route(_state(content))
        kwargs = mock_client.messages.create.call_args.kwargs
        assert kwargs["messages"] == [{"role": "user", "content": content}]
