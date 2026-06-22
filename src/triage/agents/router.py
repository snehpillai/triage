from typing import Any, Literal

import anthropic
from langchain_core.messages import HumanMessage
from langsmith.run_helpers import set_run_metadata
from langsmith.utils import tracing_is_enabled
from langsmith.wrappers import wrap_anthropic
from loguru import logger
from pydantic import BaseModel, Field

from triage.config import settings
from triage.graph.state import TicketState

# Lightweight initialisation - no network call until messages.create() is invoked.
# wrap_anthropic patches messages.create() so each call is traced as an LLM span in LangSmith.
_client = wrap_anthropic(anthropic.Anthropic(api_key=settings.anthropic_api_key))

# Single job: map ticket text to one of four intent labels.
# No downstream agent descriptions, no examples, no answering the ticket.
_SYSTEM_PROMPT = """\
You are a customer support ticket router. Classify the ticket into exactly one category:

- refund: refunds, returns, exchanges, damaged or wrong items, delivery failures
- technical: error codes, connectivity, bugs, device compatibility, outages, login failures
- billing: charges, invoices, payment failures, subscription upgrades or downgrades
- account: passwords, two-factor authentication, suspension, reactivation, data deletion

Choose the customer's primary intent. When a ticket spans multiple categories, pick the most prominent one.\
"""


class RouterOutput(BaseModel):
    intent: Literal["refund", "technical", "billing", "account"]
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str


# Built once at module load - same schema for every call.
_CLASSIFY_TOOL: dict[str, Any] = {
    "name": "classify_ticket",
    "description": "Classify a customer support ticket into the correct intent category.",
    "input_schema": RouterOutput.model_json_schema(),
}


def route(state: TicketState) -> dict[str, Any]:
    """LangGraph node: classify the ticket and populate intent + confidence.

    Also initialises the control-flow fields (retry_count, escalate,
    escalation_reason) that downstream nodes read - doing it here rather than
    in each node keeps the defaults in one place.
    """
    content = state["content"]
    ticket_id = state["ticket_id"]

    logger.debug("Router: ticket={id} | {preview}", id=ticket_id, preview=content[:80])

    response = _client.messages.create(
        model=settings.router_model,
        max_tokens=256,
        system=_SYSTEM_PROMPT,
        tools=[_CLASSIFY_TOOL],
        # Force exactly this tool - no free-text fallback, no tool selection ambiguity.
        tool_choice={"type": "tool", "name": "classify_ticket"},
        messages=[{"role": "user", "content": content}],
    )

    # tool_choice={"type": "tool", "name": "..."} guarantees a tool_use block.
    tool_block = next(b for b in response.content if b.type == "tool_use")
    output = RouterOutput.model_validate(tool_block.input)

    logger.info(
        "Router: ticket={id} intent={intent} confidence={conf:.2f}",
        id=ticket_id,
        intent=output.intent,
        conf=output.confidence,
    )
    logger.debug("Router reasoning: {r}", r=output.reasoning)
    logger.debug(
        "Router tokens: input={i} output={o}",
        i=response.usage.input_tokens,
        o=response.usage.output_tokens,
    )

    # Pre-compute the routing decision using the same threshold as _route_to_specialist.
    _THRESHOLD = 0.6
    routing_decision = "escalate" if output.confidence < _THRESHOLD else output.intent
    if tracing_is_enabled():
        set_run_metadata(
            intent=output.intent,
            confidence=round(output.confidence, 3),
            routing_decision=routing_decision,
        )

    return {
        "intent": output.intent,
        "confidence": output.confidence,
        # Initialise control-flow fields so downstream nodes never see KeyError.
        "retry_count": 0,
        "escalate": False,
        "escalation_reason": "",
        # Customer message enters the conversation history here.
        # Returning a list triggers add_messages, which appends rather than overwrites.
        "messages": [HumanMessage(content=content)],
    }
