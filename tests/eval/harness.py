"""Eval harness for the triage pipeline.

Usage (from repo root):

    # Fast mode -- deterministic mocks, no API calls, runs in seconds
    python tests/eval/harness.py --mode fast

    # Real mode -- full pipeline + Haiku judge, costs ~$5-15 for 500 tickets
    python tests/eval/harness.py --mode real

    # Limit to a subset during development
    python tests/eval/harness.py --mode real --max 50

Outputs per run:
    tests/eval/results/run_<timestamp>.json       -- per-ticket records + aggregate stats
    tests/eval/results/run_<timestamp>_summary.md -- markdown table for README
"""

import argparse
import json
import sys
import time
import uuid
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

import anthropic
from loguru import logger
from pydantic import BaseModel

from triage.config import settings
from triage.graph.builder import app as _graph

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_REPO = Path(__file__).parent.parent.parent
DATASET_DEFAULT = _REPO / "tests" / "eval" / "datasets" / "tickets_500.json"
RESULTS_DIR = _REPO / "tests" / "eval" / "results"

# ---------------------------------------------------------------------------
# Pricing constants (USD per million tokens, June 2025)
# ---------------------------------------------------------------------------

_PRICING: dict[str, dict[str, float]] = {
    "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.00},
    "claude-haiku-4-5": {"input": 0.80, "output": 4.00},
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
}

# Conservative typical token counts per pipeline call (model, input_tok, output_tok)
_TYPICAL: dict[str, tuple[str, int, int]] = {
    "router": (settings.router_model, 900, 90),
    "specialist": (settings.specialist_model, 5000, 700),
    "qc": (settings.quality_checker_model, 4500, 220),
    "escalator": (settings.quality_checker_model, 2800, 420),
    "judge": (settings.quality_checker_model, 1600, 160),
}

JUDGE_MODEL = settings.quality_checker_model

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


class JudgeVerdict(BaseModel):
    criterion_met: bool
    notes: str = ""  # Haiku occasionally omits this field; default to empty


class TicketResult(BaseModel):
    ticket_id: str
    expected_intent: str
    category: str
    should_escalate: bool
    detected_intent: str | None
    confidence: float | None
    escalated: bool
    qc_passed: bool
    retry_count: int
    final_response: str
    latency_seconds: float
    estimated_cost_usd: float
    criterion_met: bool
    judge_notes: str


class AggregateStats(BaseModel):
    total_tickets: int
    tickets_with_errors: int
    resolution_accuracy: float
    intent_accuracy: float
    escalation_accuracy: float
    false_escalation_rate: float
    missed_escalation_rate: float
    qc_rejection_rate: float
    p50_latency: float
    p95_latency: float
    p99_latency: float
    total_cost_usd: float
    cost_per_resolved_usd: float
    run_mode: str
    dataset_path: str
    timestamp: str


# ---------------------------------------------------------------------------
# Fast mode: deterministic mock pipeline + mock judge
# ---------------------------------------------------------------------------


def _mock_invoke(ticket: dict) -> tuple[dict, float]:
    """Keyword-based mock pipeline. No API calls. Tests harness structure."""
    t0 = time.monotonic()
    c = ticket["content"].lower()

    if any(
        w in c for w in ("refund", "return", "damaged", "broken", "wrong item", "never arrived")
    ):
        intent = "refund"
    elif any(
        w in c
        for w in (
            "err-",
            "error",
            "can't log in",
            "locked out",
            "browser",
            "connectivity",
            "rate limit",
        )
    ):
        intent = "technical"
    elif any(
        w in c for w in ("charge", "invoice", "billing", "payment", "subscription", "prorate")
    ):
        intent = "billing"
    else:
        intent = "account"

    escalate = ticket.get("should_escalate", False)
    response = (
        "Thank you for reaching out. We have reviewed your request carefully. "
        "Based on our policy, your request has been processed and you will receive "
        "a confirmation within one business day. Please contact us if you need "
        "further assistance. We appreciate your patience."
    )

    return {
        "intent": intent,
        "confidence": 0.45 if escalate else 0.88,
        "escalate": escalate,
        "final_response": response,
        "qc_passed": True,
        "qc_score": 8.5,
        "retry_count": 0,
        "provider": "anthropic",
        "context_docs": [],
        "tool_results": {},
    }, time.monotonic() - t0


def _mock_judge(ticket: dict, response: str) -> JudgeVerdict:
    """Deterministic mock judge. Uses ticket ID hash so results are reproducible."""
    import hashlib

    if ticket.get("should_escalate", False):
        return JudgeVerdict(
            criterion_met=True,
            notes="Mock: ambiguous ticket -- escalation is the correct outcome",
        )

    category = ticket.get("category", "happy_path")
    h = int(hashlib.md5(ticket["id"].encode()).hexdigest(), 16)

    pass_rates = {
        "happy_path": 95,
        "pii_in_input": 80,
        "hostile_tone": 75,
        "edge_case": 65,
        "ambiguous": 100,
    }
    threshold = pass_rates.get(category, 70)
    passes = (h % 100) < threshold

    return JudgeVerdict(
        criterion_met=passes,
        notes=f"Mock ({category}): deterministic result based on ticket ID",
    )


# ---------------------------------------------------------------------------
# Real mode: full pipeline invocation
# ---------------------------------------------------------------------------


def _real_invoke(ticket: dict) -> tuple[dict, float]:
    """Run the actual LangGraph pipeline for a single ticket."""
    ticket_id = str(uuid.uuid4())
    t0 = time.monotonic()
    state = _graph.invoke({"ticket_id": ticket_id, "content": ticket["content"]})
    return state, time.monotonic() - t0


# ---------------------------------------------------------------------------
# LLM judge
# ---------------------------------------------------------------------------

_JUDGE_TOOL: dict = {
    "name": "evaluate_response",
    "description": "Evaluate whether the support response satisfies the resolution criteria.",
    "input_schema": {
        "type": "object",
        "properties": {
            "criterion_met": {
                "type": "boolean",
                "description": (
                    "True only if the response satisfies ALL stated resolution criteria. "
                    "False if any required fact is missing, incorrect, or any negative criterion is violated."
                ),
            },
            "notes": {
                "type": "string",
                "description": (
                    "Specific explanation with quotes from the response. "
                    "State exactly which criteria passed or failed."
                ),
            },
        },
        "required": ["criterion_met", "notes"],
    },
}

_JUDGE_SYSTEM = """\
You are a calibrated evaluator for a customer support AI system.

Your task: decide whether the actual response satisfies the stated resolution criteria
for the ticket. Focus on SUBSTANCE, not exact wording.

Evaluation rules:
- Mark criterion_met=true if every positive criterion is substantively met AND no
  negative criterion ("must NOT") is clearly violated.
- Accept equivalent phrasing: "back to your original payment method", "to your credit
  card", and "refunded to the payment used" all satisfy "confirm refund to original
  payment method." Do not fail a response for rephrasing a requirement correctly.
- For RMA / return process steps: a response satisfies the criterion if it conveys the
  correct meaning. "The shipment must go out within 10 business days" means the
  customer ships back within 10 days - this is correct, not reversed.
- A criterion requiring X is met if the response includes X in any reasonable form.
  Only fail if the criterion fact is ABSENT or CONTRADICTED, not merely rephrased.
- A negative criterion ("must NOT") is violated only if the prohibited content is
  actually present in the response.
- If should_escalate=true and the pipeline escalated, check that the response
  acknowledges the issue and informs of human follow-up.
- If should_escalate=true but the pipeline did NOT escalate, criterion_met=false
  unless the full criteria were independently met.
- Be specific in notes: quote the exact phrase that passes or fails each criterion.
"""


def _real_judge(client: anthropic.Anthropic, ticket: dict, response: str) -> JudgeVerdict:
    prompt = (
        f"## Ticket\n{ticket['content']}\n\n"
        f"## Resolution criteria\n{ticket['resolution_criteria']}\n\n"
        f"## Actual response\n{response}\n\n"
        f"(should_escalate={ticket['should_escalate']})"
    )
    resp = client.messages.create(
        model=JUDGE_MODEL,
        max_tokens=1024,
        system=_JUDGE_SYSTEM,
        tools=[_JUDGE_TOOL],
        tool_choice={"type": "tool", "name": "evaluate_response"},
        messages=[{"role": "user", "content": prompt}],
    )
    block = next(b for b in resp.content if b.type == "tool_use")
    return JudgeVerdict.model_validate(block.input)


# ---------------------------------------------------------------------------
# Cost estimation
# ---------------------------------------------------------------------------


def _estimate_cost(state: dict, include_judge: bool = True) -> float:
    """Rough cost in USD based on typical token counts × model pricing."""
    calls: list[tuple[str, int, int]] = [
        _TYPICAL["router"],
        _TYPICAL["qc"],
    ]
    retry_count = state.get("retry_count", 0)
    for _ in range(1 + retry_count):
        if state.get("provider", "anthropic") == "openai_fallback":
            calls.append(("gpt-4o-mini", 4500, 600))
        else:
            calls.append(_TYPICAL["specialist"])
    if state.get("escalate", False):
        calls.append(_TYPICAL["escalator"])
    if include_judge:
        calls.append(_TYPICAL["judge"])

    cost = 0.0
    for model, inp, out in calls:
        p = _PRICING.get(model, {"input": 3.0, "output": 15.0})
        cost += (inp * p["input"] + out * p["output"]) / 1_000_000
    return round(cost, 6)


def _estimate_run_cost_and_time(n: int, workers: int) -> tuple[float, float]:
    avg_cost = _estimate_cost({"retry_count": 0, "provider": "anthropic", "escalate": False})
    est_cost = avg_cost * n
    # ~30s per ticket sequentially; parallelism gives linear speedup up to API limits
    effective_workers = min(workers, 8)
    est_minutes = (n * 30.0 / effective_workers) / 60
    return est_cost, est_minutes


# ---------------------------------------------------------------------------
# Per-ticket processing
# ---------------------------------------------------------------------------


def _process_ticket(
    ticket: dict,
    invoke_fn,
    judge_fn,
) -> TicketResult | None:
    try:
        state, latency = invoke_fn(ticket)

        detected_intent = state.get("intent")
        escalated = bool(state.get("escalate", False))
        final_response = state.get("final_response") or state.get("draft_response") or ""

        verdict = judge_fn(ticket, final_response)
        cost = _estimate_cost(state, include_judge=True)

        return TicketResult(
            ticket_id=ticket["id"],
            expected_intent=ticket["expected_intent"],
            category=ticket["category"],
            should_escalate=ticket["should_escalate"],
            detected_intent=detected_intent,
            confidence=state.get("confidence"),
            escalated=escalated,
            qc_passed=bool(state.get("qc_passed", True)),
            retry_count=int(state.get("retry_count", 0)),
            final_response=final_response[:1200],
            latency_seconds=round(latency, 2),
            estimated_cost_usd=cost,
            criterion_met=verdict.criterion_met,
            judge_notes=verdict.notes[:1500],
        )
    except Exception as exc:
        logger.error("Ticket {id} failed: {e}", id=ticket.get("id"), e=exc)
        return None


# ---------------------------------------------------------------------------
# Aggregate metrics
# ---------------------------------------------------------------------------


def _compute_stats(
    results: list[TicketResult],
    errors: int,
    mode: str,
    dataset_path: str,
) -> AggregateStats:
    n = len(results)
    if n == 0:
        raise RuntimeError("No results to aggregate")

    # Intent accuracy: exclude ambiguous tickets (no single correct intent)
    intent_eligible = [r for r in results if r.expected_intent != "ambiguous"]
    intent_correct = sum(1 for r in intent_eligible if r.detected_intent == r.expected_intent)
    intent_accuracy = intent_correct / len(intent_eligible) if intent_eligible else 0.0

    # Escalation accuracy: correct when escalated == should_escalate
    esc_correct = sum(1 for r in results if r.escalated == r.should_escalate)
    escalation_accuracy = esc_correct / n

    # False escalation: should NOT escalate but pipeline escalated
    should_not_esc = [r for r in results if not r.should_escalate]
    false_esc = sum(1 for r in should_not_esc if r.escalated)
    false_escalation_rate = false_esc / len(should_not_esc) if should_not_esc else 0.0

    # Missed escalation: SHOULD escalate but pipeline did not
    should_esc = [r for r in results if r.should_escalate]
    missed_esc = sum(1 for r in should_esc if not r.escalated)
    missed_escalation_rate = missed_esc / len(should_esc) if should_esc else 0.0

    # Resolution accuracy: among resolved tickets (should_not_escalate AND not_escalated),
    # what fraction had criterion_met=True?
    resolved = [r for r in results if not r.should_escalate and not r.escalated]
    resolution_correct = sum(1 for r in resolved if r.criterion_met)
    resolution_accuracy = resolution_correct / len(resolved) if resolved else 0.0

    # QC rejection rate: at least one retry OR final qc_passed=False
    qc_rejected = sum(1 for r in results if r.retry_count > 0 or not r.qc_passed)
    qc_rejection_rate = qc_rejected / n

    # Latency percentiles
    latencies = sorted(r.latency_seconds for r in results)

    def _percentile(lst: list[float], p: int) -> float:
        if not lst:
            return 0.0
        idx = max(0, min(len(lst) - 1, int(len(lst) * p / 100)))
        return lst[idx]

    # Cost
    total_cost = sum(r.estimated_cost_usd for r in results)
    cost_per_resolved = total_cost / len(resolved) if resolved else 0.0

    return AggregateStats(
        total_tickets=n,
        tickets_with_errors=errors,
        resolution_accuracy=round(resolution_accuracy, 4),
        intent_accuracy=round(intent_accuracy, 4),
        escalation_accuracy=round(escalation_accuracy, 4),
        false_escalation_rate=round(false_escalation_rate, 4),
        missed_escalation_rate=round(missed_escalation_rate, 4),
        qc_rejection_rate=round(qc_rejection_rate, 4),
        p50_latency=round(_percentile(latencies, 50), 2),
        p95_latency=round(_percentile(latencies, 95), 2),
        p99_latency=round(_percentile(latencies, 99), 2),
        total_cost_usd=round(total_cost, 4),
        cost_per_resolved_usd=round(cost_per_resolved, 6),
        run_mode=mode,
        dataset_path=dataset_path,
        timestamp=datetime.now(UTC).isoformat(),
    )


# ---------------------------------------------------------------------------
# Per-intent and per-category breakdowns for the markdown summary
# ---------------------------------------------------------------------------


def _breakdown_table(
    results: list[TicketResult],
    key: str,
) -> str:
    groups: dict[str, list[TicketResult]] = defaultdict(list)
    for r in results:
        groups[getattr(r, key)].append(r)

    header = "| {} | Tickets | Resolved | Resolution % | Escalated % | Avg Latency |\n".format(
        key.replace("_", " ").title()
    )
    sep = "|{}|---------|----------|--------------|-------------|-------------|\n".format(
        "-" * (len(key) + 2)
    )
    rows = ""
    for label, group in sorted(groups.items()):
        resolved = [r for r in group if not r.should_escalate and not r.escalated]
        res_acc = (
            sum(1 for r in resolved if r.criterion_met) / len(resolved)
            if resolved
            else float("nan")
        )
        esc_rate = sum(1 for r in group if r.escalated) / len(group)
        avg_lat = sum(r.latency_seconds for r in group) / len(group)
        res_acc_str = f"{res_acc:.1%}" if res_acc == res_acc else "N/A"
        rows += (
            f"| {label} | {len(group)} | {len(resolved)} | "
            f"{res_acc_str} | {esc_rate:.1%} | {avg_lat:.1f}s |\n"
        )
    return header + sep + rows


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------


def _write_results(results: list[TicketResult], stats: AggregateStats) -> tuple[Path, Path]:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")

    json_path = RESULTS_DIR / f"run_{ts}.json"
    md_path = RESULTS_DIR / f"run_{ts}_summary.md"

    json_path.write_text(
        json.dumps(
            {
                "stats": stats.model_dump(),
                "results": [r.model_dump() for r in results],
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n"
    )

    s = stats
    md = f"""\
# Eval Run: {ts}

**Mode:** `{s.run_mode}`
**Dataset:** `{s.dataset_path}`
**Tickets processed:** {s.total_tickets}
**Tickets with errors:** {s.tickets_with_errors}

## Summary Metrics

| Metric | Value |
|--------|-------|
| Resolution accuracy | **{s.resolution_accuracy:.1%}** |
| Intent classification accuracy | **{s.intent_accuracy:.1%}** |
| Escalation accuracy | **{s.escalation_accuracy:.1%}** |
| False escalation rate | {s.false_escalation_rate:.1%} |
| Missed escalation rate | {s.missed_escalation_rate:.1%} |
| QC rejection rate | {s.qc_rejection_rate:.1%} |
| P50 latency | {s.p50_latency:.1f}s |
| P95 latency | {s.p95_latency:.1f}s |
| P99 latency | {s.p99_latency:.1f}s |
| Total cost (estimated) | ${s.total_cost_usd:.2f} |
| Cost per resolved ticket (est.) | ${s.cost_per_resolved_usd:.4f} |

## Breakdown by Intent

{_breakdown_table(results, "expected_intent")}
## Breakdown by Category

{_breakdown_table(results, "category")}"""

    md_path.write_text(md)
    return json_path, md_path


# ---------------------------------------------------------------------------
# Progress printer
# ---------------------------------------------------------------------------


def _log_progress(completed: int, total: int, start: float) -> None:
    elapsed = time.monotonic() - start
    rate = completed / elapsed if elapsed > 0 else 0.0
    eta = (total - completed) / rate if rate > 0 else 0.0
    logger.info(
        "{done}/{total} | {rate:.1f}/s | elapsed {el:.0f}s | ETA {eta:.0f}s",
        done=completed,
        total=total,
        rate=rate,
        el=elapsed,
        eta=eta,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Triage pipeline eval harness")
    parser.add_argument(
        "--mode",
        choices=["fast", "real"],
        required=True,
        help="fast=deterministic mocks (CI), real=full pipeline (measurement run)",
    )
    parser.add_argument(
        "--dataset",
        default=str(DATASET_DEFAULT),
        help="Path to the ticket dataset JSON",
    )
    parser.add_argument(
        "--max",
        type=int,
        default=None,
        help="Limit to first N tickets (useful during development)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=6,
        help="Parallel workers for real mode (default: 6)",
    )
    args = parser.parse_args()

    dataset_path = Path(args.dataset)
    if not dataset_path.exists():
        logger.error("Dataset file not found: {p}", p=dataset_path)
        sys.exit(1)

    tickets: list[dict] = json.loads(dataset_path.read_text())
    if args.max:
        tickets = tickets[: args.max]

    logger.info("Loaded {n} tickets from {p}", n=len(tickets), p=dataset_path)

    # Pre-flight confirmation for real mode
    if args.mode == "real":
        est_cost, est_minutes = _estimate_run_cost_and_time(len(tickets), args.workers)
        print(f"\nThis will run {len(tickets)} tickets through the full pipeline.")
        print(f"Estimated cost:  ${est_cost:.2f}")
        print(f"Estimated time:  {est_minutes:.0f} minutes ({args.workers} workers)")
        print()
        answer = input("Continue? [y/N] ").strip().lower()
        if answer != "y":
            print("Aborted.")
            sys.exit(0)
        print()

    # Build per-mode functions
    if args.mode == "fast":
        invoke_fn = _mock_invoke
        judge_client = None
        judge_fn = _mock_judge
    else:
        invoke_fn = _real_invoke
        judge_client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        judge_fn = lambda t, r: _real_judge(judge_client, t, r)  # noqa: E731

    results: list[TicketResult] = []
    error_count = 0
    completed = 0
    start_time = time.monotonic()

    if args.mode == "fast" or args.workers <= 1:
        # Sequential
        for ticket in tickets:
            result = _process_ticket(ticket, invoke_fn, judge_fn)
            if result:
                results.append(result)
            else:
                error_count += 1
            completed += 1
            if completed % 25 == 0 or completed == len(tickets):
                _log_progress(completed, len(tickets), start_time)
    else:
        # Parallel (real mode)
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {pool.submit(_process_ticket, t, invoke_fn, judge_fn): t for t in tickets}
            for future in as_completed(futures):
                result = future.result()
                if result:
                    results.append(result)
                else:
                    error_count += 1
                completed += 1
                if completed % 25 == 0 or completed == len(tickets):
                    _log_progress(completed, len(tickets), start_time)

    if error_count:
        logger.warning("{n} tickets failed and are excluded from metrics", n=error_count)

    stats = _compute_stats(results, error_count, args.mode, str(dataset_path))
    json_path, md_path = _write_results(results, stats)

    s = stats
    print(f"\n{'=' * 60}")
    print(f"EVAL RESULTS  mode={s.run_mode.upper()}  n={s.total_tickets}")
    print(f"{'=' * 60}")
    print(f"Resolution accuracy:       {s.resolution_accuracy:.1%}")
    print(f"Intent accuracy:           {s.intent_accuracy:.1%}")
    print(f"Escalation accuracy:       {s.escalation_accuracy:.1%}")
    print(f"  False escalation rate:   {s.false_escalation_rate:.1%}")
    print(f"  Missed escalation rate:  {s.missed_escalation_rate:.1%}")
    print(f"QC rejection rate:         {s.qc_rejection_rate:.1%}")
    print(f"P95 latency:               {s.p95_latency:.1f}s")
    print(f"Total cost (estimated):    ${s.total_cost_usd:.2f}")
    print(f"Cost per resolved ticket:  ${s.cost_per_resolved_usd:.4f}")
    print(f"{'=' * 60}")
    print(f"\nJSON results:      {json_path}")
    print(f"Markdown summary:  {md_path}")


if __name__ == "__main__":
    main()
