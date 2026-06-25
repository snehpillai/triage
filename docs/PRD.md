# Product Requirements Document: Triage

**Author:** Sneh Suresh
**Status:** v1.0 (shipped)
**Last updated:** June 2026

---

## 1. Problem Statement

Customer support queues at mid-to-large B2C companies are dominated by a small number of repeating intent categories: refund requests, billing questions, technical issues, and account management. These tickets are time-consuming for human agents but structurally predictable: they require the same policy lookups, the same data retrieval steps, and produce responses that follow recognizable templates.

At 5,000 tickets per day, even a 60% automation rate saves roughly 3,000 agent-hours per month. The gap between that potential and what most companies achieve is not a lack of LLM capability; it is a lack of infrastructure: reliable routing, grounded retrieval, quality control, and safe fallback when the system is not confident.

Triage is a production-style reference implementation that demonstrates how to close that gap.

---

## 2. Goals

### Primary goals

- Automatically resolve common support tickets with policy-grounded, quality-verified responses.
- Escalate tickets to human agents when intent is ambiguous, confidence is low, or the quality gate fails after two attempts.
- Provide full observability into every pipeline step, including latency, cost, quality scores, and failure reasons.

### Secondary goals

- Serve as a portfolio demonstration of applied AI engineering for roles in AI product, TPM, and MLOps.
- Establish a repeatable eval harness that measures accuracy, latency, and cost across a 500-case synthetic dataset.

### Non-goals (v1)

- Multi-language support. All tickets and responses are in English.
- Real-time chat. The system is designed for async ticket workflows, not live conversation.
- Multi-tenant data isolation. The system serves a single tenant per deployment.
- Automated retraining. Model updates are manual.

---

## 3. Users and Personas

### End customer
Files a support ticket through a web form or API. Does not interact with the system directly. Receives a response within the SLA window (target: under 60 seconds for automated resolutions).

### Support manager
Monitors the escalation queue. Receives structured escalation summaries that include the ticket intent, confidence score, rejection reason, and a recommended action. Does not need to understand the pipeline internals to act on an escalation.

### Operations engineer
Monitors pipeline health via Prometheus metrics and LangSmith traces. Needs per-intent throughput, QC rejection rates, tool failure counts, and circuit breaker events. Does not interact with ticket content.

### AI / ML engineer
Maintains and improves the pipeline. Runs the eval harness to measure the impact of prompt changes. Reviews LangSmith traces to diagnose quality failures. Adjusts QC thresholds and retrieval parameters.

---

## 3.5 User Stories

### End customer

- As an end customer, I want to receive a policy-grounded response to my refund request within 60 seconds, so that I do not have to wait in a support queue for a human agent to look up the same policy document.
- As an end customer, I want the automated response to reference my actual order details (product, payment method, order status), so that I know the system has looked at my specific case and not sent a generic reply.
- As an end customer, I want to be told clearly when my ticket has been escalated to a human agent and why, so that I know what to expect next and am not left waiting on a resolved ticket that was never actually resolved.

### Support manager

- As a support manager, I want every escalated ticket to arrive with a structured summary including intent, confidence score, and rejection reason, so that my agents can act immediately without re-reading the full ticket thread.
- As a support manager, I want to see the QC rejection rate broken down by reason (PII, length, forbidden phrase, LLM judge), so that I can identify whether quality failures are systemic or isolated and prioritize prompt improvements accordingly.
- As a support manager, I want automated resolutions to be blocked from sending if they fail the compliance checklist, so that I am not liable for responses that omit legally required disclosures.

### Operations engineer

- As an operations engineer, I want per-intent ticket throughput and P95 latency available on a Prometheus-compatible endpoint, so that I can alert on SLA breaches without building custom instrumentation.
- As an operations engineer, I want circuit breaker events for each downstream tool to appear as labeled Prometheus counters, so that I can distinguish a tool-level degradation from a pipeline-wide failure during an incident.

### AI / ML engineer

- As an ML engineer, I want to run the eval harness against a fixed dataset slice before and after a prompt change, so that I can measure accuracy delta without submitting real tickets and incurring unnecessary API cost.
- As an ML engineer, I want each LangSmith trace to include the retrieved chunk scores, tool call results, and QC stage outcomes as structured metadata, so that I can diagnose a quality failure without re-running the ticket.
- As an ML engineer, I want the QC retry feedback to be specific ("Missing: RMA issued within 1 business day") rather than generic ("response is incomplete"), so that the specialist retry addresses the right gap and I can measure whether the feedback is actually effective.

---

## 4. Success Metrics

These targets were set before development began. Actuals are from eval run `run_20260623T043202Z` (best of 12 real-mode runs against 50 tickets).

| Metric | Target | Actual (best run) | Actual (avg, 12 runs) |
|---|---|---|---|
| Resolution accuracy | 70% | 68.8% | ~57% |
| Intent classification accuracy | 100% | 100% | 100% |
| False escalation rate | under 15% | 14.3% | ~18% |
| P95 end-to-end latency | under 60s | 57.5s | ~58s |
| Cost per resolved ticket | under $0.10 | $0.056 | $0.063 |
| QC rejection rate | no target set | 46% | ~47% |
| Human agent handling time per escalation | under 4 minutes (hypothetical baseline: 12 min) | not yet measured | not yet measured |
| Estimated time saved per resolved ticket vs. human baseline | 8 minutes per ticket (hypothetical) | not yet measured | not yet measured |

**Notable gap:** The 70% resolution accuracy target was not reliably crossed. The best single run was 68.8%; the average across 12 runs is approximately 57%. The 22-point swing between best (68.8%) and worst (46.2%) is caused by LLM temperature variance in the specialist: the same ticket produces a different draft each run, which determines whether the QC gate passes, which determines whether a retry fires. This is documented in `tests/eval/results/baseline_numbers.md` as-is.

---

## 5. Functional Requirements

### 5.1 Ticket ingestion

- The system SHALL accept tickets via a REST API (`POST /tickets`) and return a `ticket_id` immediately.
- The API SHALL return an HTTP 202 with the ticket ID within 200ms, regardless of pipeline duration.
- Clients SHALL poll `GET /tickets/{id}` to retrieve status and response.
- The API SHALL authenticate requests using an `X-API-Key` header in production deployments. Development mode bypasses authentication.

### 5.2 Intent classification (Router)

- The system SHALL classify every ticket into exactly one of four intent categories: `refund`, `technical`, `billing`, `account`.
- The Router SHALL output a confidence score between 0.0 and 1.0 alongside the intent.
- Tickets with confidence below 0.6 SHALL be escalated immediately without invoking a specialist.
- The Router SHALL use structured tool-use output (not free-text parsing) to guarantee schema compliance.
- If the model returns an invalid intent, the Router SHALL catch the validation error and escalate the ticket rather than propagate bad state.

### 5.3 Specialist response generation

- The system SHALL route each ticket to a specialist agent corresponding to its intent category.
- Each specialist SHALL retrieve the top-5 most relevant policy chunks from the knowledge base using vector similarity search (cosine, threshold 0.50).
- Each specialist SHALL call live tools (`order_lookup`, `account_status`) as needed to ground the response in real data.
- Specialists SHALL run an agentic loop with a maximum of 5 iterations per ticket.
- If the Anthropic API is unavailable (5xx, timeout, or rate limit), the specialist SHALL fall back to the OpenAI provider transparently.

### 5.4 Quality checking

- The Quality Checker SHALL run three stages in sequence before approving any draft:
  - **Stage 1 (hard rules):** Block responses containing PII patterns (SSN, card numbers, email addresses added by the agent), responses shorter than 50 characters or longer than 2,000 characters, responses containing any of four forbidden phrases, and responses where router confidence is below threshold.
  - **Stage 1.5 (compliance checklist):** For refund-intent non-denial responses that include return-process language, verify that all required Section 8 disclosures are present. Return targeted feedback if any are missing.
  - **Stage 2 (LLM judge):** Score the draft on a 1-10 scale across accuracy, completeness, and tone. Block drafts scoring below 7.0.
- Rejected drafts SHALL be returned to the specialist with specific feedback. The retry cap is 2.
- After two rejections, the ticket SHALL escalate.

### 5.5 Escalation

- The Escalator SHALL produce a structured summary containing: the ticket content, intent, confidence score, rejection reason (if any), and a recommended action for the human agent.
- Escalation records SHALL be persisted to the database with a timestamp and all available context.

### 5.6 Tool reliability

- Each external tool (`order_lookup`, `account_status`) SHALL be protected by a Redis-backed circuit breaker.
- After 5 failures within a 60-second window, the circuit SHALL open and block further calls to that tool for 60 seconds.
- During the open state, the specialist SHALL receive an explicit "service unavailable" message and answer from policy alone rather than fabricating data.

### 5.7 Observability

- Every ticket run SHALL produce a LangSmith trace with a `ticket_pipeline` root span and child spans for each agent.
- The worker process SHALL expose a Prometheus metrics endpoint on port 9091 with the following counters: `tickets_total`, `ticket_latency_seconds`, `qc_rejections_total`, `llm_calls_total`, `tool_failures_total`, `circuit_open_total`.

---

## 6. Non-Functional Requirements

| Requirement | Target |
|---|---|
| P95 end-to-end latency | under 60 seconds |
| API ingress response time | under 200ms |
| Cost per resolved ticket | under $0.10 |
| Uptime | 99.5% (single-region deployment) |
| PII in outbound responses | zero tolerance |
| Test coverage (unit) | 124 tests, no external dependencies, under 2 seconds |

---

## 7. System Architecture

### Pipeline overview

```
Customer ticket
     |
     v
POST /tickets  (FastAPI, returns ticket_id immediately)
     |
     v
Redis Streams queue
     |
     v
Worker consumer
     |
     v
LangGraph pipeline:
  Router (Haiku) --> confidence check
     |
     v
  Specialist (Sonnet) --> retrieval + tool calls --> draft
     |
     v
  Quality Checker (Haiku) --> Stage 1 --> Stage 1.5 --> Stage 2
     |              |
     |              v (fail + retry < 2)
     |         back to Specialist
     |              |
     v (pass or retries exhausted)
  Escalator (if needed)
     |
     v
  Persist result to Postgres
     |
     v
GET /tickets/{id}  (client polls for result)
```

![System overview](docs/diagrams/new_system_architecture.jpg)

### Key design decisions

**LangGraph for orchestration.** The retry cycle (specialist to QC to specialist) and the confidence-based conditional routing require a stateful graph with declared edges. A plain function chain cannot express this without hidden control flow.

**Hierarchical multi-agent architecture.** Four specialist agents (one per intent) rather than a single general agent. This keeps each agent's context window small, allows per-category prompt tuning, and makes per-category eval metrics meaningful.

**pgvector over a managed vector database.** At roughly 10,000 document chunks, a separate vector service adds cost and a network hop without measurable benefit. The knowledge base lives in the same Postgres instance as ticket state.

**Two-stage QC.** Deterministic rules (Stage 1 and Stage 1.5) run before the LLM judge (Stage 2). This eliminates a class of stochastic failures at zero token cost and produces specific, actionable retry feedback rather than vague LLM feedback.

**Haiku for classification and QC; Sonnet for specialists.** Classification and rule-checking do not require deep reasoning. Running Haiku at the bookends cuts cost by roughly 10x at those steps while keeping the expensive Sonnet calls focused on response generation.

**Async ingress with Redis Streams queue (local deployment) and sync mode (Modal).** Holding an HTTP connection open for 8-15 seconds at scale causes timeouts and thread exhaustion. The production-style local stack decouples ingress from processing via a queue. The Modal serverless deployment uses a sync mode flag to run the pipeline inline, since no persistent worker process is available.

---

## 8. Milestones and Delivery

The project was built in five sequential phases over approximately one week.

| Phase | Deliverable | Status |
|---|---|---|
| Day 1 | Data layer: Postgres, pgvector, SQLAlchemy models, Alembic migrations, Voyage AI embedder, knowledge base ingestion | Complete |
| Day 2 | Core pipeline: LangGraph graph, Router, Specialists, stub QC, retrieval integration, tool calls, circuit breaker, cross-provider fallback | Complete |
| Day 3 | Quality layer: QC Stage 1 hard rules, Stage 1.5 compliance checklist, Stage 2 LLM judge, retry cycle, Escalator | Complete |
| Day 4 | Infrastructure: FastAPI ingress, Redis Streams queue and worker, LangSmith tracing, Prometheus metrics, Docker Compose stack, Modal deployment, Streamlit demo | Complete |
| Day 5 | Evaluation and testing: 500-case eval harness with fast and real modes, 124 unit tests (zero API spend), integration tests, README | Complete |

---

## 9. Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| LLM temperature variance causes inconsistent resolution accuracy | High | Medium | Stage 1.5 deterministic compliance check reduces variance for the highest-volume failure case. Structured specialist output is the longer-term fix. |
| Anthropic API outage | Medium | High | Cross-provider fallback to OpenAI gpt-4o-mini. Provider field in state records which backend ran. |
| Tool (order_lookup, account_status) degradation | Medium | Medium | Redis-backed circuit breaker. Model answers from policy alone when circuit is open. |
| Runaway LLM costs | Low | High | Haiku at router and QC bookends. Hard retry cap of 2. Eval harness fast mode for zero-cost testing. |
| PII leakage in outbound responses | Low | Critical | Stage 1 hard rule blocks responses containing SSN patterns, 16-digit card numbers, and agent-added email addresses before any draft is sent. |
| Eval dataset not representative of production traffic | Medium | Medium | Dataset covers all four intent categories with edge cases (ambiguous tickets, missing order IDs, chargeback threats, hygiene-exception items). Variance analysis documented in baseline_numbers.md. |
| Support manager distrust leads to manual review of all automated responses | Medium | High | Provide a confidence score and full audit trail with every resolution. Design the escalation path to be the default for any case where the system is not certain, so managers see the system being appropriately conservative rather than overconfident. Adoption requires a trust-building rollout: start with low-stakes categories (billing inquiries) before enabling refund automation. |
| Ticket distribution shifts over time, making eval dataset unrepresentative | Medium | Medium | Tag eval cases by category and edge-case type. When production tickets deviate from the eval distribution (e.g., a product recall causes a spike in a new refund sub-type), add representative cases to the dataset before the next eval run. Treat the eval dataset as a living artifact, not a fixed benchmark. |

---

## 10. Open Questions and Future Work

The following are known gaps that were explicitly deferred from v1.

Structured specialist output is the highest-priority item because it directly addresses the root cause of the resolution accuracy gap documented in Section 4: the 22-point run-to-run variance is almost entirely caused by the specialist's free-text response stochastically omitting required disclosures, which then triggers the QC retry loop and sometimes escalation. Semantic caching is second priority because it eliminates variance for repeated scenarios at near-zero implementation cost once the output format is stable. The remaining items (Grafana, prompt versioning, ticketing integration, multi-tenancy) are infrastructure investments that improve operability and scalability but do not move the core accuracy metric.

**Structured specialist output.** The largest source of eval variance is the specialist's free-text response occasionally omitting a required disclosure. Switching to structured output with mandatory fields (`eligibility`, `policy_section`, `process_steps`, `payment_method`) would make required disclosures schema constraints rather than prompt suggestions.

**Semantic response caching.** Many tickets in a real queue are minor variations of the same scenario. Caching specialist responses keyed on (intent, retrieved chunk IDs, order facts) would eliminate redundant LLM calls and remove run-to-run variance for cached cases.

**Grafana dashboard.** The Prometheus metrics exist. A visualization layer showing tickets per minute, escalation rate over time, per-intent QC rejection breakdown, and circuit breaker events is the missing piece for operational monitoring.

**Prompt versioning.** The specialist prompt changed nine times during evaluation development. A prompt registry keyed on (agent, version, eval score) would make the improvement trajectory auditable and reversible.

**Ticketing system integration.** A Zendesk or Intercom webhook adapter would allow the system to operate as a first-pass automation layer in a real support queue, with the escalation path writing back to the source system.

**Multi-tenant isolation.** The current system serves a single tenant. Tenant-scoped knowledge bases, per-tenant prompt configurations, and data isolation at the database layer would be required for a multi-customer deployment.

---

## 11. Appendix

- Live demo: https://triagedemo.streamlit.app
- Repository: https://github.com/snehpillai/triage
- Eval results and trajectory: `tests/eval/results/baseline_numbers.md`
- Architecture diagram: `docs/diagrams/`
