"""LangGraph graph builder for the triage pipeline.

Wires together: Router -> Specialist (4 branches) -> QC -> END or Escalator.
"""

from typing import Any

from langgraph.graph import END, StateGraph

from triage.agents.escalator import Escalator
from triage.agents.quality_checker import QualityChecker
from triage.agents.router import route
from triage.agents.specialists.account import AccountSpecialist
from triage.agents.specialists.billing import BillingSpecialist
from triage.agents.specialists.refund import RefundSpecialist
from triage.agents.specialists.technical import TechnicalSpecialist
from triage.graph.state import TicketState

# Module-level instances - all are stateless, safe to share across invocations.
_refund = RefundSpecialist()
_technical = TechnicalSpecialist()
_billing = BillingSpecialist()
_account = AccountSpecialist()
_qc = QualityChecker()
_escalator = Escalator()


def _qc_node(state: TicketState) -> dict[str, Any]:
    """Run Stage 1+2 checks; promote draft on pass, increment retry_count on fail.

    Incrementing here (before the routing function reads state) means the
    routing function sees retry_count=1 after the first failure and can use
    that to decide retry vs. escalate without needing a separate increment node.
    """
    result = _qc.run(state)
    if result["qc_passed"]:
        result["final_response"] = state.get("draft_response", "")
    else:
        result["retry_count"] = state.get("retry_count", 0) + 1
    return result


# ---------------------------------------------------------------------------
# Routing functions
# ---------------------------------------------------------------------------


# Keep in sync with triage.agents.quality_checker._CONFIDENCE_THRESHOLD
_CONFIDENCE_THRESHOLD = 0.6


def _route_to_specialist(state: TicketState) -> str:
    """After the Router node, select the specialist branch by intent.

    Short-circuits to the escalator for low-confidence classifications so we
    never spend a specialist LLM call on a ticket the router couldn't classify
    reliably — the same confidence check QC Stage 1 would apply, applied early.
    """
    if state.get("confidence", 1.0) < _CONFIDENCE_THRESHOLD:
        return "escalate"
    intent = state.get("intent", "")
    if intent in ("refund", "technical", "billing", "account"):
        return intent
    # Unknown intent - escalate immediately
    return "escalate"


def _route_after_specialist(state: TicketState) -> str:
    """After a Specialist node, go to escalator or QC."""
    if state.get("escalate", False):
        return "escalate"
    return "qc"


def _route_after_qc(state: TicketState) -> str:
    """After QC: pass -> END, first fail -> retry same specialist, second fail -> escalator.

    retry_count was already incremented by _qc_node on failure, so:
      retry_count == 1  (first failure)  -> route back to specialist
      retry_count >= 2  (second failure) -> escalate
    """
    if state.get("qc_passed", True):
        return "end"
    if state.get("retry_count", 0) <= 1:
        return _route_to_specialist(state)
    return "escalate"


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------


def build_graph() -> StateGraph:
    """Compile and return the full triage StateGraph."""
    graph = StateGraph(TicketState)

    # Nodes
    graph.add_node("router", route)
    graph.add_node("refund", _refund.run)
    graph.add_node("technical", _technical.run)
    graph.add_node("billing", _billing.run)
    graph.add_node("account", _account.run)
    graph.add_node("qc", _qc_node)
    graph.add_node("escalator", _escalator.run)

    # Entry point
    graph.set_entry_point("router")

    # Router -> Specialist (conditional on intent)
    graph.add_conditional_edges(
        "router",
        _route_to_specialist,
        {
            "refund": "refund",
            "technical": "technical",
            "billing": "billing",
            "account": "account",
            "escalate": "escalator",
        },
    )

    # Each specialist -> QC or escalator
    for specialist in ("refund", "technical", "billing", "account"):
        graph.add_conditional_edges(
            specialist,
            _route_after_specialist,
            {"qc": "qc", "escalate": "escalator"},
        )

    # QC -> END, retry specialist, or escalator
    graph.add_conditional_edges(
        "qc",
        _route_after_qc,
        {
            "end": END,
            "refund": "refund",
            "technical": "technical",
            "billing": "billing",
            "account": "account",
            "escalate": "escalator",
        },
    )

    # Escalator always ends
    graph.add_edge("escalator", END)

    return graph.compile()


# Compiled graph instance - importable by FastAPI and worker processes
app = build_graph()
