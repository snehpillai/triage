"""Quality Checker - Stage 1 (hard rules) + Stage 2 (LLM-as-judge).

Stage 1 is deterministic: four rules, short-circuit on first failure.
Stage 2 only runs when Stage 1 passes: the LLM judge scores the draft
response against the retrieved policy and the original ticket.
"""

import re
from typing import Any

import anthropic
from langsmith.run_helpers import set_run_metadata
from langsmith.utils import tracing_is_enabled
from langsmith.wrappers import wrap_anthropic
from loguru import logger
from pydantic import BaseModel, Field

from triage.config import settings
from triage.graph.state import TicketState
from triage.observability.metrics import record_llm_call, record_qc_rejection
from triage.retrieval.types import ChunkWithScore

# ---------------------------------------------------------------------------
# Compiled patterns - built once at import time
# ---------------------------------------------------------------------------

_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")

# 13-19 contiguous digits not adjacent to another digit.
# Covers all major card lengths (Visa 13/16, Amex 15, MC 16, UnionPay 19).
_CARD_RE = re.compile(r"(?<!\d)\d{13,19}(?!\d)")

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")

_FORBIDDEN_PHRASES: list[str] = [
    "I don't know",
    "I cannot help with that",
    "As an AI",
    "I'm not able to",
]

_MIN_LEN = 50
_MAX_LEN = 2000
_CONFIDENCE_THRESHOLD = 0.6
_QC_PASS_THRESHOLD = 7.0
_LOW_RETRIEVAL_THRESHOLD = 0.50

# ---------------------------------------------------------------------------
# Stage 1.5 - Required disclosure checklists (category-specific)
#
# In production customer support, legal/compliance teams define required
# disclosures that must appear in responses for specific ticket types.
# Checking these programmatically catches omissions before the LLM judge
# runs, and produces targeted retry feedback ("Missing: 10-day ship-back
# window") rather than vague LLM feedback ("response is incomplete").
#
# Each entry: (regex_pattern, human-readable label for feedback)
# ---------------------------------------------------------------------------

# Phrases that indicate the specialist is denying rather than approving.
# If any appear, the Section 8 process steps do not apply.
_REFUND_DENIAL_SIGNALS: tuple[str, ...] = (
    "not eligible",
    "not qualify",
    "cannot be processed",
    "unable to process",
    "no refund",
    "does not qualify",
    "not covered",
    "ineligible",
)

# Required Section 8 process disclosures for approved refund/return responses.
# Each tuple is (compiled regex, label shown in feedback).
_REFUND_REQUIRED_DISCLOSURES: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"photo|attach|image|documentation", re.I),
        "attach photo documentation at submission time (Section 8.1)",
    ),
    (
        re.compile(r"\brma\b", re.I),
        "RMA number will be issued (Section 8.2)",
    ),
    (
        re.compile(r"1 business day|within one business day", re.I),
        "RMA issued within 1 business day (Section 8.2)",
    ),
    (
        re.compile(r"10 business day|10-business-day|ten business day", re.I),
        "item must be shipped back within 10 business days of RMA (Section 8.3)",
    ),
    (
        re.compile(r"2 business day|two business day|inspection", re.I),
        "inspection completed within 2 business days then refund initiated (Section 8.4)",
    ),
]

# ---------------------------------------------------------------------------
# Stage 2 - LLM judge schema and prompt
# ---------------------------------------------------------------------------

_client = wrap_anthropic(anthropic.Anthropic(api_key=settings.anthropic_api_key))


class QCJudgeOutput(BaseModel):
    accuracy_score: float = Field(ge=0.0, le=10.0)
    completeness_score: float = Field(ge=0.0, le=10.0)
    tone_score: float = Field(ge=0.0, le=10.0)
    overall_score: float = Field(ge=0.0, le=10.0)
    passes: bool
    feedback: str


_JUDGE_TOOL_NAME = "judge_response"
_JUDGE_TOOL: dict[str, Any] = {
    "name": _JUDGE_TOOL_NAME,
    "description": ("Score a customer support draft response on accuracy, completeness, and tone."),
    "input_schema": QCJudgeOutput.model_json_schema(),
}

_JUDGE_SYSTEM = """\
You are a quality reviewer for a customer support system. You will receive a customer
ticket, the most relevant policy chunks retrieved for that ticket, and a draft response
written by a specialist agent.

Score the draft on four dimensions (each 0.0-10.0):

- accuracy_score: Does the response correctly apply the retrieved policy? Penalise claims
  that directly contradict the policy chunks or invent policy rules not present in them.
  Do NOT penalise specific order or account details (dollar amounts, product names, order
  IDs, delivery dates, tracking numbers, payment methods) - these come from live tool
  lookups and are authoritative data. Only fail accuracy when the agent makes up a policy
  rule or contradicts one that is clearly stated in the retrieved chunks.

- completeness_score: Does the response fully address what the customer asked? Are there
  unresolved questions or missing next steps the customer needs to take?

- tone_score: Is the tone professional and empathetic? Not dismissive, not robotic, not
  overly apologetic to the point of being vague?

- overall_score: Your holistic assessment of the response quality.

Set passes=True when overall_score >= 7.0.
In the feedback field, give specific and actionable notes. If the response passes cleanly,
a single sentence of confirmation is fine. If it fails, quote the problematic part and
explain exactly what should change.
"""


def _build_judge_prompt(
    content: str,
    context_docs: list[ChunkWithScore],
    draft: str,
) -> str:
    top3 = context_docs[:3]
    if top3:
        chunks_text = "\n\n---\n\n".join(
            f"[{i + 1}] {doc.chunk.source_file} (score {doc.score:.2f})\n{doc.chunk.content}"
            for i, doc in enumerate(top3)
        )
    else:
        chunks_text = "(no policy chunks were retrieved for this ticket)"

    return (
        f"## Customer ticket\n{content}\n\n"
        f"## Retrieved policy chunks\n{chunks_text}\n\n"
        f"## Draft response\n{draft}"
    )


# ---------------------------------------------------------------------------
# QualityChecker
# ---------------------------------------------------------------------------


class QualityChecker:
    """Stage 1 hard rules then Stage 2 LLM-as-judge."""

    def run(self, state: TicketState) -> dict[str, Any]:
        """LangGraph node: run Stage 1, 1.5, then Stage 2 if both pass."""
        draft = state.get("draft_response", "")
        content = state.get("content", "")
        confidence = state.get("confidence", 1.0)
        intent = state.get("intent", "")
        context_docs: list[ChunkWithScore] = state.get("context_docs") or []
        ticket_id = state["ticket_id"]

        # Stage 1 - deterministic hard rules.
        stage1_failure: str | None = None
        stage1_reason = ""
        for check_fn, reason in (
            (lambda: self._check_pii(draft, content), "pii"),
            (lambda: self._check_length(draft), "length"),
            (lambda: self._check_forbidden_phrases(draft), "forbidden_phrase"),
            (lambda: self._check_confidence(confidence), "low_confidence"),
        ):
            failure = check_fn()
            if failure:
                stage1_failure = failure
                stage1_reason = reason
                break

        if stage1_failure:
            logger.warning("QC Stage 1 failed ticket={id}: {msg}", id=ticket_id, msg=stage1_failure)
            record_qc_rejection(stage1_reason)
            if tracing_is_enabled():
                set_run_metadata(stage1_passed=False, stage1_failure_reason=stage1_failure)
            return {"qc_score": 0.0, "qc_feedback": stage1_failure, "qc_passed": False}

        if tracing_is_enabled():
            set_run_metadata(stage1_passed=True)
        logger.info("QC Stage 1 passed ticket={id}", id=ticket_id)

        # Stage 1.5 - category-specific required disclosure check.
        # Deterministic, zero LLM cost. Catches omitted compliance-required
        # details (e.g. the Section 8 return process steps for refund tickets)
        # and returns targeted feedback so the specialist retry is surgical.
        stage15_failure = self._check_required_disclosures(intent, draft)
        if stage15_failure:
            logger.warning(
                "QC Stage 1.5 failed ticket={id}: {msg}", id=ticket_id, msg=stage15_failure
            )
            record_qc_rejection("missing_disclosure")
            if tracing_is_enabled():
                set_run_metadata(stage15_passed=False, stage15_failure_reason=stage15_failure)
            return {"qc_score": 0.0, "qc_feedback": stage15_failure, "qc_passed": False}

        logger.info("QC Stage 1.5 passed ticket={id}", id=ticket_id)

        # Log retrieval scores before calling the judge
        if context_docs:
            scores = [f"{d.score:.3f}" for d in context_docs]
            logger.debug("QC retrieval scores ticket={id}: {scores}", id=ticket_id, scores=scores)

        # Stage 2 - LLM judge
        return self._run_judge(ticket_id, content, context_docs, draft)

    # ------------------------------------------------------------------
    # Stage 1 checks - each returns a failure string or None
    # ------------------------------------------------------------------

    def _check_pii(self, draft: str, content: str) -> str | None:
        if _SSN_RE.search(draft):
            return "PII detected in response"
        if _CARD_RE.search(draft):
            return "PII detected in response"
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

    def _check_required_disclosures(self, intent: str, draft: str) -> str | None:
        """Stage 1.5: verify category-specific required disclosures are present.

        Only runs when the response is an approval, not a denial. Returns a
        feedback string listing every missing item, or None if all are present.
        """
        if intent != "refund":
            return None

        draft_lower = draft.lower()

        # Skip the check for denial responses - Section 8 steps only apply
        # when a return/refund is being approved and the customer needs to act.
        if any(signal in draft_lower for signal in _REFUND_DENIAL_SIGNALS):
            return None

        # The check also only fires when the response is directing the customer
        # through a return process. If "return" / "ship" / "rma" aren't present
        # at all, this is probably a timeline question or status inquiry, not
        # a process walkthrough.
        if not any(kw in draft_lower for kw in ("return", "ship", "rma", "send back")):
            return None

        missing = [
            label for pattern, label in _REFUND_REQUIRED_DISCLOSURES if not pattern.search(draft)
        ]

        if not missing:
            return None

        return (
            "Response is missing required return-process disclosures. "
            "Add the following to your response: " + "; ".join(missing) + "."
        )

    # ------------------------------------------------------------------
    # Stage 2 - LLM judge
    # ------------------------------------------------------------------

    def _run_judge(
        self,
        ticket_id: str,
        content: str,
        context_docs: list[ChunkWithScore],
        draft: str,
    ) -> dict[str, Any]:
        prompt = _build_judge_prompt(content, context_docs, draft)

        response = _client.messages.create(
            model=settings.quality_checker_model,
            max_tokens=800,
            system=_JUDGE_SYSTEM,
            tools=[_JUDGE_TOOL],
            tool_choice={"type": "tool", "name": _JUDGE_TOOL_NAME},
            messages=[{"role": "user", "content": prompt}],
        )

        tool_block = next(b for b in response.content if b.type == "tool_use")
        output = QCJudgeOutput.model_validate(tool_block.input)
        record_llm_call(agent="qc", model=settings.quality_checker_model, provider="anthropic")

        # Enforce the threshold programmatically - don't trust the LLM's boolean.
        passes = output.overall_score >= _QC_PASS_THRESHOLD
        if not passes:
            record_qc_rejection("llm_judge")
        feedback = output.feedback

        if tracing_is_enabled():
            set_run_metadata(
                stage2_overall_score=round(output.overall_score, 2),
                stage2_passed=passes,
                stage2_accuracy=round(output.accuracy_score, 2),
                stage2_completeness=round(output.completeness_score, 2),
                stage2_tone=round(output.tone_score, 2),
            )

        logger.info(
            "QC Stage 2 ticket={id} overall={s:.1f} passes={p} "
            "accuracy={a:.1f} completeness={c:.1f} tone={t:.1f}",
            id=ticket_id,
            s=output.overall_score,
            p=passes,
            a=output.accuracy_score,
            c=output.completeness_score,
            t=output.tone_score,
        )
        logger.debug(
            "QC judge tokens: input={i} output={o}",
            i=response.usage.input_tokens,
            o=response.usage.output_tokens,
        )

        # Append low-retrieval warning to feedback regardless of pass/fail.
        if context_docs and context_docs[0].score < _LOW_RETRIEVAL_THRESHOLD:
            warning = (
                f"Top retrieval score was {context_docs[0].score:.2f} - "
                "response may be poorly grounded."
            )
            logger.warning("QC retrieval warning ticket={id}: {w}", id=ticket_id, w=warning)
            feedback = f"{feedback}\n{warning}".strip() if feedback else warning

        return {"qc_score": output.overall_score, "qc_feedback": feedback, "qc_passed": passes}
