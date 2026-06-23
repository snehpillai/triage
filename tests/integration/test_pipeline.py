"""End-to-end pipeline integration tests.

Invokes the compiled LangGraph graph directly - same path the worker takes.
Requires real Anthropic and Voyage API keys plus a Postgres DB with the
knowledge base ingested.

Run with:
    pytest -m integration

These tests are excluded from the default pytest run to avoid surprise API spend.
"""

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def graph():
    """Import and return the compiled graph once per module."""
    from triage.graph.builder import app as _graph

    return _graph


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _invoke(graph, ticket_id: str, content: str) -> dict:
    return graph.invoke({"ticket_id": ticket_id, "content": content})


def _assert_base_shape(state: dict) -> None:
    assert isinstance(state.get("final_response"), str), "final_response must be a string"
    assert len(state["final_response"]) > 0, "final_response must not be empty"
    assert isinstance(state.get("escalate"), bool), "escalate must be a bool"
    assert state.get("intent") in (
        "refund",
        "technical",
        "billing",
        "account",
    ), f"unexpected intent: {state.get('intent')}"


# ---------------------------------------------------------------------------
# One test per intent type
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_refund_intent_resolves(graph) -> None:
    state = _invoke(
        graph,
        "integ-refund-01",
        "My order ORD-1001 arrived with a cracked screen. I need a full refund.",
    )
    _assert_base_shape(state)
    assert state["intent"] == "refund"
    assert state["qc_passed"] is True or state["escalate"] is True


@pytest.mark.integration
def test_technical_intent_resolves(graph) -> None:
    state = _invoke(
        graph,
        "integ-tech-01",
        "I keep getting an 'invalid session' error when I try to log in. "
        "I have cleared my cache and tried three different browsers.",
    )
    _assert_base_shape(state)
    assert state["intent"] == "technical"


@pytest.mark.integration
def test_billing_intent_resolves(graph) -> None:
    state = _invoke(
        graph,
        "integ-billing-01",
        "My monthly subscription was $24.99 this month instead of the usual $9.99. "
        "I did not change my plan. Please explain the charge.",
    )
    _assert_base_shape(state)
    assert state["intent"] == "billing"


@pytest.mark.integration
def test_account_intent_resolves(graph) -> None:
    state = _invoke(
        graph,
        "integ-account-01",
        "I want to permanently delete my account and all my personal data. " "How do I do this?",
    )
    _assert_base_shape(state)
    assert state["intent"] == "account"
