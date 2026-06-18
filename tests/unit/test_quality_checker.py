"""Unit tests for QualityChecker Stage 1 (hard rules) and Stage 2 (LLM judge).

Stage 1 tests are fully deterministic - no mocking needed.
Stage 2 tests mock triage.agents.quality_checker._client to avoid real API calls
and to exercise the threshold enforcement and retrieval-warning logic.
"""

from unittest.mock import MagicMock, patch

import pytest

from triage.agents.quality_checker import QualityChecker
from triage.retrieval.types import ChunkWithScore

_QC = QualityChecker()

# A valid draft that passes every rule - used as a baseline in mutation tests.
_GOOD_DRAFT = (
    "Thank you for reaching out. Based on our refund policy, your request for order "
    "ORD-1001 is within the 30-day return window and qualifies for a full refund. "
    "Please submit your request through the account portal with the damaged item photos "
    "attached. You will receive an RMA number within one business day."
)


def _state(
    draft: str,
    content: str = "I need help with my order.",
    confidence: float = 0.95,
    context_docs: list | None = None,
) -> dict:
    return {
        "ticket_id": "t-test",
        "content": content,
        "draft_response": draft,
        "confidence": confidence,
        "context_docs": context_docs or [],
    }


def _mock_judge_response(
    overall: float = 9.0,
    accuracy: float = 9.0,
    completeness: float = 9.0,
    tone: float = 9.0,
    feedback: str = "Response is accurate and complete.",
) -> MagicMock:
    """Build a minimal mock Anthropic response for the Stage 2 judge tool call."""
    block = MagicMock()
    block.type = "tool_use"
    block.input = {
        "accuracy_score": accuracy,
        "completeness_score": completeness,
        "tone_score": tone,
        "overall_score": overall,
        "passes": overall >= 7.0,
        "feedback": feedback,
    }
    resp = MagicMock()
    resp.content = [block]
    resp.usage.input_tokens = 100
    resp.usage.output_tokens = 50
    return resp


def _mock_chunk(score: float) -> ChunkWithScore:
    """Build a minimal ChunkWithScore stub with a given similarity score.

    Uses model_construct to skip Pydantic's isinstance check on DocumentChunk,
    which would reject a MagicMock. The QC only reads .score and .chunk.source_file
    on these objects, both of which MagicMock satisfies.
    """
    chunk = MagicMock()
    chunk.source_file = "refund_policy.md"
    chunk.content = "Policy content."
    return ChunkWithScore.model_construct(chunk=chunk, score=score)


# ---------------------------------------------------------------------------
# PII - SSN
# ---------------------------------------------------------------------------


class TestSSNCheck:
    def test_ssn_pattern_rejected(self) -> None:
        result = _QC.run(_state(f"Your SSN 123-45-6789 is on file. {_GOOD_DRAFT}"))
        assert result["qc_passed"] is False
        assert result["qc_feedback"] == "PII detected in response"
        assert result["qc_score"] == 0.0

    def test_partial_ssn_not_rejected(self) -> None:
        # "123-45" is not a full SSN pattern
        result = _QC.run(_state(f"Reference code 123-45 applies here. {_GOOD_DRAFT}"))
        assert result["qc_feedback"] != "PII detected in response"

    def test_ssn_in_different_positions(self) -> None:
        for snippet in [
            "SSN: 000-00-0000 confirmed",
            "social 999-88-7777 on record",
        ]:
            result = _QC.run(_state(f"{snippet} {_GOOD_DRAFT}"))
            assert result["qc_passed"] is False, f"should catch: {snippet!r}"


# ---------------------------------------------------------------------------
# PII - Credit card
# ---------------------------------------------------------------------------


class TestCreditCardCheck:
    def test_16_digit_card_rejected(self) -> None:
        result = _QC.run(_state(f"Card 4111111111111111 was charged. {_GOOD_DRAFT}"))
        assert result["qc_passed"] is False
        assert result["qc_feedback"] == "PII detected in response"

    def test_15_digit_amex_rejected(self) -> None:
        result = _QC.run(_state(f"Amex card 371449635398431 on file. {_GOOD_DRAFT}"))
        assert result["qc_passed"] is False

    def test_13_digit_card_rejected(self) -> None:
        result = _QC.run(_state(f"Card number 4000001234560 charged. {_GOOD_DRAFT}"))
        assert result["qc_passed"] is False

    def test_12_digits_not_rejected(self) -> None:
        # 12 digits is below the minimum card length
        result = _QC.run(_state(f"Reference 123456789012 applies. {_GOOD_DRAFT}"))
        assert result["qc_feedback"] != "PII detected in response"

    def test_20_digits_not_rejected(self) -> None:
        # 20 consecutive digits exceeds max card length - should not match
        result = _QC.run(_state(f"ID 12345678901234567890 logged. {_GOOD_DRAFT}"))
        assert result["qc_feedback"] != "PII detected in response"


# ---------------------------------------------------------------------------
# PII - Email
# ---------------------------------------------------------------------------


class TestEmailCheck:
    def test_new_email_in_draft_rejected(self) -> None:
        result = _QC.run(
            _state(
                f"Please contact agent@company.com for follow-up. {_GOOD_DRAFT}",
                content="I need help with my order.",
            )
        )
        assert result["qc_passed"] is False
        assert result["qc_feedback"] == "PII detected in response"

    def test_customer_own_email_echoed_back_allowed(self) -> None:
        # The customer mentioned their email in the ticket - echoing it is fine.
        result = _QC.run(
            _state(
                f"We will send your refund confirmation to customer@example.com. {_GOOD_DRAFT}",
                content="My email is customer@example.com, please help with my refund.",
            )
        )
        assert result["qc_feedback"] != "PII detected in response"

    def test_multiple_new_emails_rejected(self) -> None:
        result = _QC.run(
            _state(
                f"Contact a@x.com or b@y.com. {_GOOD_DRAFT}",
                content="Help me.",
            )
        )
        assert result["qc_passed"] is False

    def test_no_emails_passes(self) -> None:
        result = _QC.run(_state(_GOOD_DRAFT))
        assert result["qc_feedback"] != "PII detected in response"


# ---------------------------------------------------------------------------
# Length check
# ---------------------------------------------------------------------------


class TestLengthCheck:
    def test_too_short_rejected(self) -> None:
        result = _QC.run(_state("OK."))
        assert result["qc_passed"] is False
        assert result["qc_feedback"] == "Response too short, likely incomplete"

    def test_exactly_50_chars_passes(self) -> None:
        draft = "x" * 50
        result = _QC.run(_state(draft))
        assert result["qc_feedback"] != "Response too short, likely incomplete"

    def test_49_chars_rejected(self) -> None:
        draft = "x" * 49
        result = _QC.run(_state(draft))
        assert result["qc_passed"] is False
        assert result["qc_feedback"] == "Response too short, likely incomplete"

    def test_too_long_rejected(self) -> None:
        draft = "x" * 2001
        result = _QC.run(_state(draft))
        assert result["qc_passed"] is False
        assert result["qc_feedback"] == "Response too long, likely rambling"

    def test_exactly_2000_chars_passes(self) -> None:
        draft = "x" * 2000
        result = _QC.run(_state(draft))
        assert result["qc_feedback"] != "Response too long, likely rambling"

    def test_2001_chars_rejected(self) -> None:
        draft = "x" * 2001
        result = _QC.run(_state(draft))
        assert result["qc_passed"] is False


# ---------------------------------------------------------------------------
# Forbidden phrases
# ---------------------------------------------------------------------------


class TestForbiddenPhrases:
    @pytest.mark.parametrize(
        "phrase",
        [
            "I don't know",
            "I cannot help with that",
            "As an AI",
            "I'm not able to",
        ],
    )
    def test_exact_phrase_rejected(self, phrase: str) -> None:
        result = _QC.run(_state(f"{_GOOD_DRAFT} {phrase} the rest."))
        assert result["qc_passed"] is False
        assert "cop-out phrasing" in result["qc_feedback"]
        assert phrase in result["qc_feedback"]

    @pytest.mark.parametrize(
        "phrase",
        [
            "i don't know",
            "AS AN AI",
            "I Cannot Help With That",
            "I'M NOT ABLE TO",
        ],
    )
    def test_case_insensitive_match(self, phrase: str) -> None:
        result = _QC.run(_state(f"{_GOOD_DRAFT} {phrase} more text."))
        assert result["qc_passed"] is False
        assert "cop-out phrasing" in result["qc_feedback"]

    def test_clean_response_passes(self) -> None:
        result = _QC.run(_state(_GOOD_DRAFT))
        assert "cop-out phrasing" not in result["qc_feedback"]


# ---------------------------------------------------------------------------
# Confidence threshold
# ---------------------------------------------------------------------------


class TestConfidenceCheck:
    def test_below_threshold_rejected(self) -> None:
        result = _QC.run(_state(_GOOD_DRAFT, confidence=0.59))
        assert result["qc_passed"] is False
        assert result["qc_feedback"] == "Router confidence below threshold, escalating for review"

    def test_exactly_at_threshold_rejected(self) -> None:
        # Threshold is < 0.6, so 0.6 itself should pass
        result = _QC.run(_state(_GOOD_DRAFT, confidence=0.6))
        assert result["qc_feedback"] != "Router confidence below threshold, escalating for review"

    def test_above_threshold_passes(self) -> None:
        result = _QC.run(_state(_GOOD_DRAFT, confidence=0.9))
        assert result["qc_feedback"] != "Router confidence below threshold, escalating for review"

    def test_zero_confidence_rejected(self) -> None:
        result = _QC.run(_state(_GOOD_DRAFT, confidence=0.0))
        assert result["qc_passed"] is False


# ---------------------------------------------------------------------------
# Short-circuit ordering - PII blocks before length, length before phrases
# ---------------------------------------------------------------------------


class TestShortCircuit:
    def test_pii_wins_over_short_length(self) -> None:
        # SSN present but also too short - PII should be reported, not length
        result = _QC.run(_state("SSN 123-45-6789"))
        assert result["qc_feedback"] == "PII detected in response"

    def test_length_wins_over_forbidden_phrase(self) -> None:
        # Too short AND contains forbidden phrase - length fires first
        result = _QC.run(_state("I don't know."))
        assert result["qc_feedback"] == "Response too short, likely incomplete"

    def test_forbidden_phrase_wins_over_low_confidence(self) -> None:
        # Forbidden phrase AND low confidence - phrase fires first
        result = _QC.run(_state(f"{_GOOD_DRAFT} As an AI I have limitations.", confidence=0.5))
        assert "cop-out phrasing" in result["qc_feedback"]


# ---------------------------------------------------------------------------
# Stage 2 - LLM judge (mocked)
# ---------------------------------------------------------------------------


class TestStage2Judge:
    def test_passing_score_returns_passed(self) -> None:
        with patch("triage.agents.quality_checker._client") as mock_client:
            mock_client.messages.create.return_value = _mock_judge_response(overall=8.5)
            result = _QC.run(_state(_GOOD_DRAFT))
        assert result["qc_passed"] is True
        assert result["qc_score"] == 8.5

    def test_failing_score_returns_failed(self) -> None:
        with patch("triage.agents.quality_checker._client") as mock_client:
            mock_client.messages.create.return_value = _mock_judge_response(
                overall=5.0, feedback="Response misquotes the refund window."
            )
            result = _QC.run(_state(_GOOD_DRAFT))
        assert result["qc_passed"] is False
        assert result["qc_score"] == 5.0
        assert "misquotes" in result["qc_feedback"]

    def test_threshold_enforced_at_exactly_7(self) -> None:
        with patch("triage.agents.quality_checker._client") as mock_client:
            mock_client.messages.create.return_value = _mock_judge_response(overall=7.0)
            result = _QC.run(_state(_GOOD_DRAFT))
        assert result["qc_passed"] is True

    def test_threshold_enforced_below_7(self) -> None:
        with patch("triage.agents.quality_checker._client") as mock_client:
            # Even if the LLM sets passes=True, we override with the programmatic threshold.
            block = MagicMock()
            block.type = "tool_use"
            block.input = {
                "accuracy_score": 6.9,
                "completeness_score": 6.9,
                "tone_score": 6.9,
                "overall_score": 6.9,
                "passes": True,  # LLM said pass - we should override
                "feedback": "Barely missed.",
            }
            resp = MagicMock()
            resp.content = [block]
            resp.usage.input_tokens = 100
            resp.usage.output_tokens = 50
            mock_client.messages.create.return_value = resp
            result = _QC.run(_state(_GOOD_DRAFT))
        assert result["qc_passed"] is False

    def test_low_retrieval_score_appends_warning(self) -> None:
        low_score_docs = [_mock_chunk(0.42)]
        with patch("triage.agents.quality_checker._client") as mock_client:
            mock_client.messages.create.return_value = _mock_judge_response(
                overall=8.0, feedback="Good response."
            )
            result = _QC.run(_state(_GOOD_DRAFT, context_docs=low_score_docs))
        assert result["qc_passed"] is True  # warning doesn't cause failure
        assert "0.42" in result["qc_feedback"]
        assert "poorly grounded" in result["qc_feedback"]

    def test_high_retrieval_score_no_warning(self) -> None:
        high_score_docs = [_mock_chunk(0.65)]
        with patch("triage.agents.quality_checker._client") as mock_client:
            mock_client.messages.create.return_value = _mock_judge_response(
                overall=8.0, feedback="Good response."
            )
            result = _QC.run(_state(_GOOD_DRAFT, context_docs=high_score_docs))
        assert "poorly grounded" not in result["qc_feedback"]

    def test_stage2_not_called_when_stage1_fails(self) -> None:
        # Stage 1 should short-circuit without ever touching the Anthropic client.
        with patch("triage.agents.quality_checker._client") as mock_client:
            _QC.run(_state("too short"))
            mock_client.messages.create.assert_not_called()


# ---------------------------------------------------------------------------
# Happy path - mocked Stage 2
# ---------------------------------------------------------------------------


class TestHappyPath:
    def test_clean_response_passes_all_stages(self) -> None:
        with patch("triage.agents.quality_checker._client") as mock_client:
            mock_client.messages.create.return_value = _mock_judge_response(overall=9.0)
            result = _QC.run(_state(_GOOD_DRAFT))
        assert result["qc_passed"] is True
        assert result["qc_score"] == 9.0
