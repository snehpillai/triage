"""End-to-end smoke test for the Day 2 refund pipeline.

Invokes the compiled LangGraph with three refund tickets and prints a
structured summary of each run: intent, retrieval chunks, tool calls,
and the final draft response.

Run from the project root:
    python scripts/test_day2_pipeline.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from triage.graph.builder import app  # noqa: E402 (must come after sys.path insert)

_SEPARATOR = "=" * 72

_TICKETS = [
    (
        "T-D2-001",
        "I want a refund on order ORD-1001, it arrived damaged",
    ),
    (
        "T-D2-002",
        "My order ORD-1003 never showed up and it's been 3 weeks",
    ),
    (
        "T-D2-003",
        "I changed my mind, can I return a digital download I bought last week?",
    ),
]


def _print_ticket(ticket_id: str, content: str, state: dict) -> None:
    print(f"\n{_SEPARATOR}")
    print(f"TICKET {ticket_id}")
    print(f"INPUT : {content}")
    print(_SEPARATOR)

    # --- Router output ---
    print("\n[Router]")
    print(f"  intent     : {state.get('intent', 'N/A')}")
    print(f"  confidence : {state.get('confidence', 0.0):.2f}")

    # --- Retrieval ---
    context_docs = state.get("context_docs") or []
    print(f"\n[Retrieval]  {len(context_docs)} chunk(s)")
    for i, doc in enumerate(context_docs, 1):
        first_line = doc.chunk.content.strip().splitlines()[0][:100]
        print(f"  [{i}] {doc.chunk.source_file}  score={doc.score:.3f}")
        print(f"       {first_line!r}")

    # --- Tool calls ---
    tool_results = state.get("tool_results") or {}
    if tool_results:
        print(f"\n[Tool calls]  {len(tool_results)} call(s)")
        for name, result in tool_results.items():
            if hasattr(result, "model_dump"):
                data = result.model_dump()
            else:
                data = result
            print(f"  {name}() ->")
            if isinstance(data, dict):
                for k, v in data.items():
                    print(f"    {k}: {v}")
            else:
                print(f"    {data}")
    else:
        print("\n[Tool calls]  none")

    # --- Escalation or draft ---
    if state.get("escalate"):
        print("\n[ESCALATED]")
        print(f"  reason: {state.get('escalation_reason', '')}")
        return

    response = state.get("final_response") or state.get("draft_response") or ""
    print("\n[Draft response]")
    print(response)


def main() -> None:
    print("Building graph...")
    # app is already compiled at import time; this just confirms the import worked
    print(f"Nodes: {list(app.nodes.keys())}\n")

    for ticket_id, content in _TICKETS:
        state = app.invoke({"ticket_id": ticket_id, "content": content})
        _print_ticket(ticket_id, content, state)

    print(f"\n{_SEPARATOR}")
    print("Done.")


if __name__ == "__main__":
    main()
