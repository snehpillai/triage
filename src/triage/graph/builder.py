"""LangGraph graph builder for the triage pipeline.

Wires together: Router -> Specialist (4 branches) -> [QC placeholder] -> END.

The QC node and Escalator are stubbed as pass-through nodes so the graph
compiles and runs end-to-end today. Day 3 replaces the stubs with real logic.
"""

from typing import Any

from langgraph.graph import END, StateGraph

from triage.agents.router import route
from triage.agents.specialists.account import AccountSpecialist
from triage.agents.specialists.billing import BillingSpecialist
from triage.agents.specialists.refund import RefundSpecialist
from triage.agents.specialists.technical import TechnicalSpecialist
from triage.graph.state import TicketState

# Module-level specialist instances (stateless, safe to share)
_refund = RefundSpecialist()
_technical = TechnicalSpecialist()
_billing = BillingSpecialist()
_account = AccountSpecialist()


# ---------------------------------------------------------------------------
# Stub nodes - replaced in Day 3
# ---------------------------------------------------------------------------


def _qc_node(state: TicketState) -> dict[str, Any]:
    """Placeholder QC node: passes all drafts through unconditionally."""
    return {
        "qc_score": 10.0,
        "qc_feedback": "",
        "qc_passed": True,
        "final_response": state.get("draft_response", ""),
    }


def _escalator_node(state: TicketState) -> dict[str, Any]:
    """Placeholder escalator: writes a canned escalation message."""
    reason = state.get("escalation_reason", "No reason provided")
    return {
        "final_response": (f"Your request has been escalated to a human agent. Reason: {reason}")
    }


# ---------------------------------------------------------------------------
# Routing functions
# ---------------------------------------------------------------------------


def _route_to_specialist(state: TicketState) -> str:
    """After the Router node, select the specialist branch by intent."""
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
    """After QC, go to END if passed, or escalate if not (Day 3: retry logic)."""
    if not state.get("qc_passed", True):
        return "escalate"
    return "end"


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
    graph.add_node("escalator", _escalator_node)

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

    # QC -> end or escalator
    graph.add_conditional_edges(
        "qc",
        _route_after_qc,
        {"end": END, "escalate": "escalator"},
    )

    # Escalator always ends
    graph.add_edge("escalator", END)

    return graph.compile()


# Compiled graph instance - importable by FastAPI and worker processes
app = build_graph()
