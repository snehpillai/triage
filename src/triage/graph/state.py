from typing import Annotated, Any, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

from triage.retrieval.types import ChunkWithScore


class _TicketStateRequired(TypedDict):
    """Fields that must be present when the graph is invoked."""

    ticket_id: str
    content: str  # raw customer message


class TicketState(_TicketStateRequired, total=False):
    """Complete state carried through the graph.

    Required at invocation: ticket_id, content.
    Everything else is populated by nodes as the graph runs.

    total=False makes all fields in this class (not the parent) optional so
    LangGraph can merge partial updates without type errors.
    """

    # Set by Router
    intent: str  # one of: refund | technical | billing | account
    confidence: float  # Router's confidence in its classification (0.0-1.0)

    # Set by Specialist after retrieval and tool calls
    context_docs: list[ChunkWithScore]
    tool_results: dict[str, Any]
    draft_response: str

    # Set by Quality Checker (wired in Day 3)
    qc_score: float  # 0.0-10.0; threshold for pass is 7.0
    qc_feedback: str  # reason for rejection when qc_passed is False
    qc_passed: bool

    # Control flow - nodes initialise these before first use
    retry_count: int  # starts at 0; capped at 1 before forcing escalation
    escalate: bool  # True routes to Escalator instead of final response
    escalation_reason: str

    # Set after QC passes (Day 3)
    final_response: str

    # Which LLM provider produced the draft: "anthropic" | "openai_fallback"
    provider: str

    # LangGraph message history.
    # add_messages reducer appends new messages rather than overwriting the list,
    # which is the correct behaviour for a multi-turn agent conversation.
    messages: Annotated[list[BaseMessage], add_messages]
