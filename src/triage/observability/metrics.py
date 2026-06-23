"""Prometheus metric definitions and increment helpers for the triage pipeline.

Six metrics, six helper functions. The rest of the codebase calls only the
helpers - never .inc() / .observe() directly - so instrumentation lives in
one place and is straightforward to test or refactor.
"""

from prometheus_client import Counter, Histogram

# ---------------------------------------------------------------------------
# Metric definitions
# ---------------------------------------------------------------------------

tickets_total = Counter(
    "tickets_total",
    "Total tickets processed, labelled by intent and final status.",
    ["intent", "status"],  # status: resolved | escalated | failed
)

ticket_latency_seconds = Histogram(
    "ticket_latency_seconds",
    "End-to-end pipeline duration per ticket.",
    ["intent"],
    buckets=(1, 2, 4, 8, 15, 30, 60),
)

qc_rejections_total = Counter(
    "qc_rejections_total",
    "Quality-check rejections by failure reason.",
    ["reason"],  # pii | length | forbidden_phrase | low_confidence | llm_judge
)

llm_calls_total = Counter(
    "llm_calls_total",
    "LLM API calls completed, labelled by pipeline agent, model, and provider.",
    ["agent", "model", "provider"],  # provider: anthropic | openai
)

tool_failures_total = Counter(
    "tool_failures_total",
    "Tool invocation failures (exceptions raised during tool execution).",
    ["tool_name"],
)

circuit_open_total = Counter(
    "circuit_open_total",
    "Calls blocked because the circuit breaker was open.",
    ["tool_name"],
)


# ---------------------------------------------------------------------------
# Public helpers - call these from pipeline nodes, not the metric objects
# ---------------------------------------------------------------------------


def record_ticket_outcome(intent: str, status: str) -> None:
    """Increment tickets_total after a ticket reaches its terminal state."""
    tickets_total.labels(intent=intent, status=status).inc()


def record_ticket_latency(intent: str, duration_seconds: float) -> None:
    """Observe ticket_latency_seconds for a completed (or failed) ticket."""
    ticket_latency_seconds.labels(intent=intent).observe(duration_seconds)


def record_qc_rejection(reason: str) -> None:
    """Increment qc_rejections_total when Stage 1 or Stage 2 rejects a draft."""
    qc_rejections_total.labels(reason=reason).inc()


def record_llm_call(agent: str, model: str, provider: str) -> None:
    """Increment llm_calls_total after a successful LLM response is received."""
    llm_calls_total.labels(agent=agent, model=model, provider=provider).inc()


def record_tool_failure(tool_name: str) -> None:
    """Increment tool_failures_total when a tool raises an exception."""
    tool_failures_total.labels(tool_name=tool_name).inc()


def record_circuit_open(tool_name: str) -> None:
    """Increment circuit_open_total when a CircuitOpenError is caught."""
    circuit_open_total.labels(tool_name=tool_name).inc()
