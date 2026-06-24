"""Triage demo UI.

Run with:
    streamlit run demo/app.py

Environment variables:
    TRIAGE_API_URL     FastAPI base URL (default: http://localhost:8000)
    TRIAGE_METRICS_URL Prometheus metrics base URL (default: http://localhost:9091)
    TRIAGE_API_KEY     X-API-Key header value (required when ENVIRONMENT=production)
"""

import os
import re
import time

import requests
import streamlit as st

API_URL = os.getenv("TRIAGE_API_URL", "http://localhost:8000").rstrip("/")
METRICS_URL = os.getenv("TRIAGE_METRICS_URL", "http://localhost:9091").rstrip("/")
_API_KEY = os.getenv("TRIAGE_API_KEY", "")
_HEADERS = {"X-API-Key": _API_KEY} if _API_KEY else {}

SCENARIOS: dict[str, str] = {
    "Damaged item refund": (
        "Hi, I received my order ORD-1001 yesterday and the tablet was completely "
        "shattered when I opened the box. There is visible damage to the screen and "
        "the casing is cracked. I need a full refund immediately. This is completely "
        "unacceptable and I am very disappointed."
    ),
    "Non-delivery refund": (
        "I ordered a laptop (ORD-1002) over two weeks ago and it still has not arrived. "
        "The tracking number shows it left the warehouse but there has been no update "
        "for 10 days. I believe my package is lost. Please refund my $899.99."
    ),
    "Digital download return": (
        "I purchased an e-book last week and I want a refund. I have already read about "
        "half of it and I am not satisfied with the content. Please process my return."
    ),
    "Login broken after password reset": (
        "I reset my password yesterday but now I cannot log in at all. I get an "
        "'invalid credentials' error even with the new password. I have tried on "
        "Chrome and Firefox and cleared my cache. I am completely locked out of my account."
    ),
    "Unexpected charge increase": (
        "I just got my credit card statement and my subscription charge was $24.99 "
        "instead of the usual $9.99 I pay every month. I did not change anything on "
        "my account. What happened and can you fix this?"
    ),
    "How do I delete my account?": (
        "I want to permanently delete my account and all my personal data. "
        "Please tell me the process for doing this and confirm that all my "
        "information will be fully removed from your systems."
    ),
    "I have a problem (ambiguous)": (
        "Something is wrong with my account. I have been having issues for a few "
        "days now and I really need help sorting this out as soon as possible."
    ),
}

INTENT_COLORS: dict[str, str] = {
    "refund": "#e74c3c",
    "technical": "#3498db",
    "billing": "#f39c12",
    "account": "#2ecc71",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _confidence_badge(value: float | None) -> str:
    if value is None:
        return "N/A"
    pct = int(value * 100)
    if pct >= 80:
        color = "#27ae60"
    elif pct >= 60:
        color = "#f39c12"
    else:
        color = "#e74c3c"
    return (
        f'<span style="background:{color};color:#fff;padding:2px 10px;'
        f'border-radius:12px;font-size:0.85rem;font-weight:600">{pct}%</span>'
    )


def _submit_ticket(content: str) -> str:
    # 90s timeout: sync-mode pipeline takes 8-15s; allow headroom for cold starts.
    resp = requests.post(
        f"{API_URL}/tickets",
        json={"content": content},
        headers=_HEADERS,
        timeout=90,
    )
    resp.raise_for_status()
    return resp.json()["ticket_id"]


def _poll_ticket(ticket_id: str) -> dict:
    resp = requests.get(f"{API_URL}/tickets/{ticket_id}", headers=_HEADERS, timeout=10)
    resp.raise_for_status()
    return resp.json()


def _parse_prometheus(text: str) -> dict[str, list[tuple[str, float]]]:
    metrics: dict[str, list[tuple[str, float]]] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # metric_name{labels} value [timestamp]  OR  metric_name value
        parts = line.split()
        if len(parts) < 2:
            continue
        try:
            value = float(parts[-2]) if len(parts) >= 3 else float(parts[-1])
        except ValueError:
            continue
        raw_name = parts[0]
        name = raw_name.split("{")[0]
        metrics.setdefault(name, []).append((raw_name, value))
    return metrics


def _fetch_metrics() -> dict[str, list[tuple[str, float]]]:
    try:
        resp = requests.get(f"{METRICS_URL}/metrics", timeout=3)
        resp.raise_for_status()
        return _parse_prometheus(resp.text)
    except Exception:
        return {}


def _extract_label(raw: str, key: str) -> str | None:
    m = re.search(rf'{key}="([^"]+)"', raw)
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# Page layout
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Triage: Multi-Agent Support Demo",
    page_icon=":ticket:",
    layout="wide",
)

# Tighten font size so the full resolved trace fits in one screenshot.
st.markdown(
    "<style>"
    "section[data-testid='stMain'] { font-size: 0.88rem; }"
    ".stExpander p, .stExpander li { font-size: 0.85rem; }"
    "</style>",
    unsafe_allow_html=True,
)

st.title("Triage: Multi-Agent Customer Support")
st.caption(
    "Submits tickets to the FastAPI pipeline, polls until resolved, "
    "and shows the full agent trace."
)

# ---------------------------------------------------------------------------
# Section A - Input panel
# ---------------------------------------------------------------------------

st.subheader("Submit a ticket")

scenario_names = ["(type your own)"] + list(SCENARIOS.keys())
selected = st.selectbox("Pick a demo scenario", scenario_names)

default_text = SCENARIOS.get(selected, "")
content = st.text_area(
    "Ticket content",
    value=default_text,
    height=130,
    placeholder="Describe the customer's issue...",
)

submitted = st.button("Submit ticket", type="primary", disabled=not content.strip())

# ---------------------------------------------------------------------------
# Submit handler
# ---------------------------------------------------------------------------

if submitted and content.strip():
    with st.spinner("Pipeline running... this takes 10-15 seconds"):
        try:
            ticket_id = _submit_ticket(content.strip())
            st.session_state["ticket_id"] = ticket_id
            st.session_state["result"] = None
            st.session_state["timed_out"] = False
        except Exception as exc:
            st.error(f"Failed to submit ticket: {exc}")

# ---------------------------------------------------------------------------
# Section B - Processing / result panel
# ---------------------------------------------------------------------------

if "ticket_id" in st.session_state:
    ticket_id: str = st.session_state["ticket_id"]
    st.divider()
    st.subheader("Pipeline result")
    st.caption(f"Ticket ID: `{ticket_id}`")

    result = st.session_state.get("result")

    if result is None and not st.session_state.get("timed_out", False):
        with st.spinner("Processing..."):
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                try:
                    data = _poll_ticket(ticket_id)
                    if data["status"] in ("resolved", "escalated", "failed"):
                        result = data
                        st.session_state["result"] = result
                        break
                except Exception:
                    pass
                time.sleep(1)
            else:
                st.session_state["timed_out"] = True

    if st.session_state.get("timed_out") and result is None:
        st.warning(f"Pipeline is still running - check back shortly. Ticket ID: `{ticket_id}`")
    elif result:
        status = result.get("status", "unknown")
        escalated = result.get("escalated", False)

        # Status badge
        if status == "resolved" and not escalated:
            st.success("Resolved - response sent to customer")
        elif escalated or status == "escalated":
            st.warning("Escalated - routed to a human agent")
        else:
            st.info(f"Status: {status}")

        debug = result.get("debug_info") or {}

        # --- Routing decision ---
        with st.expander("Routing decision", expanded=True):
            intent = result.get("intent") or debug.get("intent") or "unknown"
            confidence = debug.get("confidence")
            color = INTENT_COLORS.get(intent, "#888")
            badge = _confidence_badge(confidence)
            st.markdown(
                f"**Intent:** "
                f'<span style="background:{color};color:#fff;padding:2px 10px;'
                f'border-radius:12px;font-size:0.85rem;font-weight:600">'
                f"{intent}</span>&nbsp;&nbsp;"
                f"**Confidence:** {badge}",
                unsafe_allow_html=True,
            )
            provider = debug.get("provider")
            if provider:
                st.caption(f"LLM provider: {provider}")

        # --- Retrieved policy ---
        context_docs: list[dict] = debug.get("context_docs") or []
        with st.expander(f"Retrieved policy ({min(3, len(context_docs))} chunks)"):
            if not context_docs:
                st.caption(
                    "No retrieval data available. Ticket may have been processed before debug_info was added."
                )
            for doc in context_docs[:3]:
                src = doc.get("source_file", "unknown")
                score = doc.get("score", 0.0)
                body = doc.get("content", "")
                st.markdown(
                    f"**{src}** &nbsp; `similarity={score:.3f}`",
                    unsafe_allow_html=True,
                )
                st.markdown(f"> {body.strip()[:400].replace(chr(10), '  ')}")

        # --- Tool calls ---
        tool_results: dict = debug.get("tool_results") or {}
        with st.expander(f"Tool calls ({len(tool_results)})"):
            if not tool_results:
                st.caption("No tools were invoked for this ticket.")
            for tool_name, tool_result in tool_results.items():
                st.markdown(f"**{tool_name}**")
                if isinstance(tool_result, dict):
                    for k, v in tool_result.items():
                        st.markdown(f"- **{k}:** {v}")
                else:
                    st.text(str(tool_result))

        # --- Quality check ---
        with st.expander("Quality check"):
            qc_passed = debug.get("qc_passed")
            qc_score = debug.get("qc_score")
            qc_feedback = debug.get("qc_feedback")
            retry_count = debug.get("retry_count", 0)

            if qc_passed is None:
                st.caption("No QC data available.")
            else:
                col1, col2 = st.columns(2)
                with col1:
                    st.metric("QC score", f"{qc_score:.1f}/10" if qc_score is not None else "N/A")
                with col2:
                    st.metric("Retries", retry_count)
                if qc_passed:
                    st.success("Passed quality check")
                else:
                    st.error("Failed quality check (escalated after retries)")
                if qc_feedback:
                    st.markdown(f"**Feedback:** {qc_feedback}")

        # --- Final response ---
        st.divider()
        if escalated:
            st.markdown("**Escalation message sent to customer:**")
            escalation_reason = result.get("escalation_reason")
            if escalation_reason:
                st.info(escalation_reason)
        else:
            st.markdown("**Response sent to customer:**")

        response_text = result.get("response") or ""
        if response_text:
            with st.container(border=True):
                st.markdown(response_text)
        else:
            st.caption("No response text available.")

# ---------------------------------------------------------------------------
# Section C - Live metrics (sidebar)
# ---------------------------------------------------------------------------

with st.sidebar:
    st.header("Live metrics")
    st.caption(f"Source: `{METRICS_URL}/metrics`")

    if st.button("Refresh metrics"):
        st.rerun()

    metrics = _fetch_metrics()

    if not metrics:
        st.caption("Metrics unavailable (worker not running in this deployment).")
    else:
        # Total tickets
        total_raw = metrics.get("tickets_total", [])
        total_tickets = sum(v for _, v in total_raw)

        # Breakdown by intent
        intent_counts: dict[str, float] = {}
        for raw_name, v in total_raw:
            intent = _extract_label(raw_name, "intent")
            if intent:
                intent_counts[intent] = intent_counts.get(intent, 0) + v

        # Escalation rate
        escalated_raw = metrics.get("tickets_total", [])
        escalated_count = sum(
            v for raw_name, v in escalated_raw if _extract_label(raw_name, "status") == "escalated"
        )

        # QC rejections
        qc_rejections = sum(v for _, v in metrics.get("qc_rejections_total", []))

        # Avg latency (sum/count)
        lat_sum = sum(v for _, v in metrics.get("ticket_latency_seconds_sum", []))
        lat_count = sum(v for _, v in metrics.get("ticket_latency_seconds_count", []))
        avg_latency = lat_sum / lat_count if lat_count > 0 else None

        st.metric("Tickets processed", int(total_tickets) if total_tickets else "N/A")

        esc_rate = f"{escalated_count / total_tickets * 100:.1f}%" if total_tickets > 0 else "N/A"
        st.metric("Escalation rate", esc_rate)
        st.metric(
            "Avg latency",
            f"{avg_latency:.1f}s" if avg_latency else "N/A",
        )
        st.metric("QC rejections", int(qc_rejections) if qc_rejections else "N/A")

        if intent_counts:
            st.markdown("**Tickets by intent**")
            import pandas as pd

            df = pd.DataFrame(
                {"intent": list(intent_counts.keys()), "count": list(intent_counts.values())}
            ).set_index("intent")
            st.bar_chart(df)
        else:
            st.caption("No intent data yet.")
