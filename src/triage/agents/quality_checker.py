"""Quality Checker - Stage 1: deterministic hard rules.

Four checks run in order, short-circuiting on the first failure.
Stage 2 (LLM-as-judge) is added in the next step and runs only when
all Stage 1 rules pass.
"""

import re
from typing import Any

from loguru import logger

from triage.graph.state import TicketState

# ---------------------------------------------------------------------------
# Compiled patterns - built once at import time
# ---------------------------------------------------------------------------

_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")

# 13-19 contiguous digits not adjacent to another digit.
# Covers all major card number lengths (Visa 13/16, Amex 15, MC 16, UnionPay 19).
_CARD_RE = re.compile(r"(?<!\d)\d{13,19}(?!\d)")

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")

# Stored in display casing so the feedback string reads naturally.
_FORBIDDEN_PHRASES: list[str] = [
    "I don't know",
    "I cannot help with that",
    "As an AI",
    "I'm not able to",
]

_MIN_LEN = 50
_MAX_LEN = 2000
_CONFIDENCE_THRESHOLD = 0.6


class QualityChecker:
    """Deterministic Stage 1 quality gate. No LLM call."""

    def run(self, state: TicketState) -> dict[str, Any]:
        """LangGraph node: apply hard rules to draft_response."""
        draft = state.get("draft_response", "")
        content = state.get("content", "")
        confidence = state.get("confidence", 1.0)
        ticket_id = state["ticket_id"]

        failure = (
            self._check_pii(draft, content)
            or self._check_length(draft)
            or self._check_forbidden_phrases(draft)
            or self._check_confidence(confidence)
        )

        if failure:
            logger.warning("QC hard-rule failed ticket={id}: {msg}", id=ticket_id, msg=failure)
            return {"qc_score": 0.0, "qc_feedback": failure, "qc_passed": False}

        logger.info("QC Stage 1 passed ticket={id}", id=ticket_id)
        return {"qc_score": 10.0, "qc_feedback": "", "qc_passed": True}

    # ------------------------------------------------------------------
    # Private checks - each returns a failure string or None
    # ------------------------------------------------------------------

    def _check_pii(self, draft: str, content: str) -> str | None:
        if _SSN_RE.search(draft):
            return "PII detected in response"
        if _CARD_RE.search(draft):
            return "PII detected in response"
        # Emails present in the draft that were not already in the customer's message
        # are unexpected and likely represent leaked PII from a tool or DB lookup.
        new_emails = set(_EMAIL_RE.findall(draft)) - set(_EMAIL_RE.findall(content))
        if new_emails:
            return "PII detected in response"
        return None

    def _check_length(self, draft: str) -> str | None:
        if len(draft) < _MIN_LEN:
            return "Response too short, likely incomplete"
        if len(draft) > _MAX_LEN:
            return "Response too long, likely rambling"
        return None

    def _check_forbidden_phrases(self, draft: str) -> str | None:
        lower = draft.lower()
        for phrase in _FORBIDDEN_PHRASES:
            if phrase.lower() in lower:
                return f"Response contains cop-out phrasing: {phrase}"
        return None

    def _check_confidence(self, confidence: float) -> str | None:
        if confidence < _CONFIDENCE_THRESHOLD:
            return "Router confidence below threshold, escalating for review"
        return None
