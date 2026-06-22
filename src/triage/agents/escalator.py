"""Escalator agent.

Generates a structured handoff summary for human agents, writes an
EscalationRecord row to the database, and returns a customer-facing
acknowledgment as the final_response.
"""

import json
import uuid
from typing import Any

import anthropic
from langsmith.run_helpers import set_run_metadata
from langsmith.utils import tracing_is_enabled
from langsmith.wrappers import wrap_anthropic
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from triage.config import settings
from triage.db.models import EscalationRecord
from triage.graph.state import TicketState

_client = wrap_anthropic(anthropic.Anthropic(api_key=settings.anthropic_api_key))
_engine = create_engine(settings.database_url)

_SLA = "24 hours"
_CUSTOMER_ACK = (
    "Thanks for your message - this needs a closer look. "
    f"A team member will follow up within {_SLA}."
)

# ---------------------------------------------------------------------------
# Structured summary schema
# ---------------------------------------------------------------------------


class EscalationSummary(BaseModel):
    intent: str
    confidence: float
    policy_sources: list[str]  # "filename (score=0.57)" - titles only, no full text
    tool_calls_summary: list[str]  # "tool_name: one-line result summary"
    draft_response_excerpt: str  # first 300 chars of rejected draft, or empty
    escalation_reason: str  # QC feedback or specialist-generated reason
    recommended_action: str  # what the human agent should do next


_SUMMARY_TOOL_NAME = "write_escalation_summary"
_SUMMARY_TOOL: dict[str, Any] = {
    "name": _SUMMARY_TOOL_NAME,
    "description": (
        "Write a structured handoff note for a human customer support agent "
        "picking up an escalated ticket."
    ),
    "input_schema": EscalationSummary.model_json_schema(),
}

_SUMMARY_SYSTEM = """\
You are writing a structured handoff note for a human customer support agent.
A ticket has been escalated from the automated pipeline. Summarise the key facts
concisely so the agent can understand the situation without reading the full history.

Be specific about:
- What the customer asked
- What the automated system tried and why it was insufficient
- What the human agent should verify or do next

Keep each list entry short (one sentence max). The draft_response_excerpt should be
truncated at 300 characters if present.
"""


def _build_summary_prompt(state: TicketState) -> str:
    content = state["content"]
    intent = state.get("intent", "unknown")
    confidence = state.get("confidence", 0.0)
    context_docs = state.get("context_docs") or []
    tool_results = state.get("tool_results") or {}
    draft = state.get("draft_response", "")
    qc_feedback = state.get("qc_feedback", "")
    escalation_reason = state.get("escalation_reason", "")

    reason = qc_feedback or escalation_reason or "No specific reason recorded"

    sources = [f"{doc.chunk.source_file} (score={doc.score:.2f})" for doc in context_docs]

    tools_text = ""
    if tool_results:
        for name, result in tool_results.items():
            if hasattr(result, "model_dump"):
                tools_text += f"\n- {name}: {json.dumps(result.model_dump())}"
            else:
                tools_text += f"\n- {name}: {result}"
    else:
        tools_text = "\n- (no tool calls were made)"

    return (
        f"## Customer ticket\n{content}\n\n"
        f"## Classification\nintent={intent}  confidence={confidence:.2f}\n\n"
        f"## Policy sources retrieved\n"
        + ("\n".join(f"- {s}" for s in sources) if sources else "- (none retrieved)")
        + f"\n\n## Tool calls{tools_text}\n\n"
        f"## Draft response (if any)\n{draft[:300] or '(none generated)'}\n\n"
        f"## Escalation reason\n{reason}"
    )


# ---------------------------------------------------------------------------
# Escalator
# ---------------------------------------------------------------------------


class Escalator:
    """Generates a human-readable escalation summary and persists it to DB."""

    def run(self, state: TicketState) -> dict[str, Any]:
        """LangGraph node: generate summary, write to DB, return acknowledgment."""
        ticket_id = state["ticket_id"]
        qc_feedback = state.get("qc_feedback", "")
        specialist_reason = state.get("escalation_reason", "")
        reason = qc_feedback or specialist_reason or "Escalated by automated system"
        confidence = state.get("confidence", None)

        # Derive which pipeline step triggered the escalation for the trace.
        if qc_feedback:
            escalation_source = "qc"
        elif specialist_reason:
            escalation_source = "specialist"
        else:
            escalation_source = "pre_specialist_routing"

        if tracing_is_enabled():
            set_run_metadata(
                escalation_reason=reason[:200],
                escalation_source=escalation_source,
                confidence=round(confidence, 3) if confidence is not None else None,
            )

        logger.info("Escalator: ticket={id} reason={r!r}", id=ticket_id, r=reason[:120])

        # 1. Generate structured summary via Haiku
        summary = self._generate_summary(state)

        # 2. Write EscalationRecord to DB (best-effort - never fail the graph)
        self._write_record(ticket_id, reason, confidence, summary)

        return {
            "escalate": True,
            "escalation_reason": reason,
            "final_response": _CUSTOMER_ACK,
        }

    def _generate_summary(self, state: TicketState) -> EscalationSummary:
        prompt = _build_summary_prompt(state)

        response = _client.messages.create(
            model=settings.quality_checker_model,
            max_tokens=512,
            system=_SUMMARY_SYSTEM,
            tools=[_SUMMARY_TOOL],
            tool_choice={"type": "tool", "name": _SUMMARY_TOOL_NAME},
            messages=[{"role": "user", "content": prompt}],
        )

        tool_block = next(b for b in response.content if b.type == "tool_use")
        summary = EscalationSummary.model_validate(tool_block.input)

        logger.debug(
            "Escalator: summary generated intent={i} sources={s} tools={t}",
            i=summary.intent,
            s=len(summary.policy_sources),
            t=len(summary.tool_calls_summary),
        )
        logger.debug(
            "Escalator: tokens input={i} output={o}",
            i=response.usage.input_tokens,
            o=response.usage.output_tokens,
        )

        return summary

    def _write_record(
        self,
        ticket_id: str,
        reason: str,
        confidence: float | None,
        summary: EscalationSummary,
    ) -> None:
        # ticket_id in state is a plain string; EscalationRecord.ticket_id is a UUID FK.
        # Parse it here - if it fails or the ticket row doesn't exist, log and skip.
        try:
            tid = uuid.UUID(ticket_id)
        except ValueError:
            logger.warning(
                "Escalator: ticket_id={id!r} is not a valid UUID, skipping DB write",
                id=ticket_id,
            )
            return

        record = EscalationRecord(
            ticket_id=tid,
            reason=reason[:500],
            confidence_score=confidence,
            context_summary=summary.model_dump_json(),
        )

        try:
            with Session(_engine) as session:
                session.add(record)
                session.commit()
                logger.info(
                    "Escalator: wrote EscalationRecord id={rid} for ticket={tid}",
                    rid=record.id,
                    tid=ticket_id,
                )
        except Exception as exc:
            logger.error(
                "Escalator: DB write failed for ticket={id}: {e}",
                id=ticket_id,
                e=str(exc),
            )
