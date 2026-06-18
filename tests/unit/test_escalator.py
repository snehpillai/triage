"""Unit tests for the Escalator agent.

All tests mock triage.agents.escalator._client and the SQLAlchemy Session
to avoid real API calls and DB writes.
"""

import json
import uuid
from unittest.mock import MagicMock, patch

from triage.agents.escalator import _CUSTOMER_ACK, EscalationSummary, Escalator

_ESC = Escalator()

_VALID_UUID = str(uuid.uuid4())

_BASE_STATE: dict = {
    "ticket_id": _VALID_UUID,
    "content": "I have not received my refund after 30 days.",
    "intent": "refund",
    "confidence": 0.92,
    "context_docs": [],
    "tool_results": {},
    "draft_response": "We processed your refund on the 28th.",
    "qc_feedback": "Response does not cite the 30-day return policy.",
    "escalation_reason": "",
    "retry_count": 2,
}


def _mock_summary_response(summary: dict | None = None) -> MagicMock:
    """Build a minimal Anthropic response mock that returns the escalation summary tool call."""
    if summary is None:
        summary = {
            "intent": "refund",
            "confidence": 0.92,
            "policy_sources": ["refund_policy.md (score=0.78)"],
            "tool_calls_summary": [],
            "draft_response_excerpt": "We processed your refund on the 28th.",
            "escalation_reason": "Response does not cite the 30-day return policy.",
            "recommended_action": "Verify refund status and re-send with policy citation.",
        }
    block = MagicMock()
    block.type = "tool_use"
    block.input = summary
    resp = MagicMock()
    resp.content = [block]
    resp.usage.input_tokens = 200
    resp.usage.output_tokens = 80
    return resp


# ---------------------------------------------------------------------------
# Customer-facing acknowledgment
# ---------------------------------------------------------------------------


class TestCustomerAcknowledgment:
    def test_final_response_is_acknowledgment_not_summary(self) -> None:
        with (
            patch("triage.agents.escalator._client") as mock_client,
            patch("triage.agents.escalator.Session"),
        ):
            mock_client.messages.create.return_value = _mock_summary_response()
            result = _ESC.run(_BASE_STATE)
        assert result["final_response"] == _CUSTOMER_ACK

    def test_ack_does_not_contain_internal_details(self) -> None:
        with (
            patch("triage.agents.escalator._client") as mock_client,
            patch("triage.agents.escalator.Session"),
        ):
            mock_client.messages.create.return_value = _mock_summary_response()
            result = _ESC.run(_BASE_STATE)
        # The customer should never see QC scores, ticket IDs, or policy source names.
        assert "qc" not in result["final_response"].lower()
        assert "score" not in result["final_response"].lower()
        assert _VALID_UUID not in result["final_response"]


# ---------------------------------------------------------------------------
# State keys set on return
# ---------------------------------------------------------------------------


class TestReturnKeys:
    def test_escalate_set_to_true(self) -> None:
        with (
            patch("triage.agents.escalator._client") as mock_client,
            patch("triage.agents.escalator.Session"),
        ):
            mock_client.messages.create.return_value = _mock_summary_response()
            result = _ESC.run(_BASE_STATE)
        assert result["escalate"] is True

    def test_escalation_reason_prefers_qc_feedback(self) -> None:
        state = {**_BASE_STATE, "qc_feedback": "tone was too robotic", "escalation_reason": "other"}
        with (
            patch("triage.agents.escalator._client") as mock_client,
            patch("triage.agents.escalator.Session"),
        ):
            mock_client.messages.create.return_value = _mock_summary_response()
            result = _ESC.run(state)
        assert result["escalation_reason"] == "tone was too robotic"

    def test_escalation_reason_falls_back_to_specialist_reason(self) -> None:
        state = {**_BASE_STATE, "qc_feedback": "", "escalation_reason": "hit iteration cap"}
        with (
            patch("triage.agents.escalator._client") as mock_client,
            patch("triage.agents.escalator.Session"),
        ):
            mock_client.messages.create.return_value = _mock_summary_response()
            result = _ESC.run(state)
        assert result["escalation_reason"] == "hit iteration cap"

    def test_escalation_reason_default_when_both_empty(self) -> None:
        state = {**_BASE_STATE, "qc_feedback": "", "escalation_reason": ""}
        with (
            patch("triage.agents.escalator._client") as mock_client,
            patch("triage.agents.escalator.Session"),
        ):
            mock_client.messages.create.return_value = _mock_summary_response()
            result = _ESC.run(state)
        assert result["escalation_reason"] == "Escalated by automated system"


# ---------------------------------------------------------------------------
# Haiku summary call
# ---------------------------------------------------------------------------


class TestSummaryGeneration:
    def test_haiku_is_called_once(self) -> None:
        with (
            patch("triage.agents.escalator._client") as mock_client,
            patch("triage.agents.escalator.Session"),
        ):
            mock_client.messages.create.return_value = _mock_summary_response()
            _ESC.run(_BASE_STATE)
        mock_client.messages.create.assert_called_once()

    def test_forced_tool_choice_used(self) -> None:
        with (
            patch("triage.agents.escalator._client") as mock_client,
            patch("triage.agents.escalator.Session"),
        ):
            mock_client.messages.create.return_value = _mock_summary_response()
            _ESC.run(_BASE_STATE)
        call_kwargs = mock_client.messages.create.call_args.kwargs
        assert call_kwargs["tool_choice"] == {"type": "tool", "name": "write_escalation_summary"}

    def test_summary_parses_into_escalation_summary_model(self) -> None:
        with (
            patch("triage.agents.escalator._client") as mock_client,
            patch("triage.agents.escalator.Session"),
        ):
            mock_client.messages.create.return_value = _mock_summary_response()
            # _generate_summary should return a valid EscalationSummary
            summary = _ESC._generate_summary(_BASE_STATE)
        assert isinstance(summary, EscalationSummary)
        assert summary.intent == "refund"


# ---------------------------------------------------------------------------
# DB write behaviour
# ---------------------------------------------------------------------------


class TestDBWrite:
    def test_db_write_called_for_valid_uuid(self) -> None:
        with (
            patch("triage.agents.escalator._client") as mock_client,
            patch("triage.agents.escalator.Session") as mock_session_cls,
        ):
            mock_client.messages.create.return_value = _mock_summary_response()
            mock_session = MagicMock()
            mock_session_cls.return_value.__enter__ = lambda s: mock_session
            mock_session_cls.return_value.__exit__ = MagicMock(return_value=False)
            _ESC.run(_BASE_STATE)
        mock_session.add.assert_called_once()
        mock_session.commit.assert_called_once()

    def test_db_write_skipped_for_non_uuid_ticket_id(self) -> None:
        state = {**_BASE_STATE, "ticket_id": "T-D2-001"}
        with (
            patch("triage.agents.escalator._client") as mock_client,
            patch("triage.agents.escalator.Session") as mock_session_cls,
        ):
            mock_client.messages.create.return_value = _mock_summary_response()
            _ESC.run(state)
        # Session should never have been entered
        mock_session_cls.assert_not_called()

    def test_db_failure_does_not_raise(self) -> None:
        with (
            patch("triage.agents.escalator._client") as mock_client,
            patch("triage.agents.escalator.Session") as mock_session_cls,
        ):
            mock_client.messages.create.return_value = _mock_summary_response()
            mock_session = MagicMock()
            mock_session.commit.side_effect = Exception("DB down")
            mock_session_cls.return_value.__enter__ = lambda s: mock_session
            mock_session_cls.return_value.__exit__ = MagicMock(return_value=False)
            # Should not raise - DB write is best-effort
            result = _ESC.run(_BASE_STATE)
        assert result["final_response"] == _CUSTOMER_ACK

    def test_context_summary_is_valid_json(self) -> None:
        """The context_summary column should store valid JSON."""
        with (
            patch("triage.agents.escalator._client") as mock_client,
            patch("triage.agents.escalator.Session") as mock_session_cls,
        ):
            mock_client.messages.create.return_value = _mock_summary_response()
            mock_session = MagicMock()
            mock_session_cls.return_value.__enter__ = lambda s: mock_session
            mock_session_cls.return_value.__exit__ = MagicMock(return_value=False)
            _ESC.run(_BASE_STATE)

        record = mock_session.add.call_args[0][0]
        parsed = json.loads(record.context_summary)
        assert parsed["intent"] == "refund"
        assert "policy_sources" in parsed

    def test_reason_truncated_to_500_chars(self) -> None:
        long_reason = "x" * 600
        state = {**_BASE_STATE, "qc_feedback": long_reason}
        with (
            patch("triage.agents.escalator._client") as mock_client,
            patch("triage.agents.escalator.Session") as mock_session_cls,
        ):
            mock_client.messages.create.return_value = _mock_summary_response()
            mock_session = MagicMock()
            mock_session_cls.return_value.__enter__ = lambda s: mock_session
            mock_session_cls.return_value.__exit__ = MagicMock(return_value=False)
            _ESC.run(state)

        record = mock_session.add.call_args[0][0]
        assert len(record.reason) == 500
