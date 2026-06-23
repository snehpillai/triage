# Triage: Multi-Agent Customer Support System

A production-style customer support automation system built with LangGraph and the Anthropic API. Incoming tickets are classified, routed to specialized agents that retrieve relevant policy documents and call live tools, reviewed by a quality gate, and escalated to humans when confidence is low.

Designed for 5,000 tickets/day. Evaluated against 500 simulated tickets with end-to-end metrics.

---

## How it works

![System Overview](customer_support_system_overview.svg)

Each Specialist (Sonnet-class model) retrieves relevant policy and FAQ documents via pgvector, then calls tools to look up real order or account data before generating a response. The Quality Checker (Haiku-class model) scores the response and blocks it if it fails hard rules or an LLM-as-judge threshold. The Escalator handles graceful handoff when the system is not confident.

---

## Stack

| Layer | Technology |
|---|---|
| Agent orchestration | LangGraph |
| LLM | Anthropic API (Haiku + Sonnet) |
| Vector search | pgvector on Postgres |
| Embeddings | Voyage AI |
| Queue | Redis Streams |
| API | FastAPI (async) |
| Observability | LangSmith + Prometheus |
| Demo UI | Streamlit |
| Deployment | Modal / Railway |

---

## Project layout

```
src/triage/
├── agents/
│   ├── router.py           # Classifies ticket intent (Haiku, forced tool_choice)
│   ├── specialists/        # One agent per intent category (Sonnet, agentic loop)
│   │   └── base.py         # Shared loop: retrieval, tool calls, OAI fallback
│   ├── quality_checker.py  # Stage 1 rules + Stage 2 LLM-as-judge (Haiku)
│   └── escalator.py        # Generates EscalationSummary, writes DB record
├── graph/
│   ├── state.py            # Shared LangGraph state definition
│   └── builder.py          # Nodes, edges, retry cycle, confidence guard
├── retrieval/              # Voyage AI embedder + pgvector retrieval
├── tools/
│   ├── order_lookup.py     # Live order data tool
│   ├── account_status.py   # Live account data tool
│   └── circuit_breaker.py  # Redis-backed per-tool circuit breaker
├── api/                    # FastAPI ingress
├── queue/                  # Redis Streams producer/consumer
├── db/                     # SQLAlchemy models + Alembic migrations
├── observability/          # LangSmith setup, Prometheus metrics
└── config.py               # Pydantic Settings, env-driven
tests/
├── unit/                   # Fast, no external deps (FakeRedis for circuit breaker)
├── integration/            # Hits real DB/Redis; includes cross-provider fallback tests
└── eval/                   # 500-case evaluation harness
scripts/
├── hello_claude.py          # API smoke test (verifies API key and basic LLM call)
├── ingest_docs.py           # Chunk and embed knowledge base
├── test_day2_pipeline.py    # Day 2 end-to-end smoke test (refund pipeline)
└── test_day3_pipeline.py    # Day 3 verification: happy path, QC retry, low-confidence
demo/
└── app.py                  # Streamlit demo UI
```

---

## Evaluation results

Evaluated against the first 50 tickets of a 500-case dataset using a two-mode harness:
`--mode fast` (deterministic keyword mock + MD5 judge, zero API calls) and `--mode real`
(full pipeline + Haiku judge). Full trajectory and root-cause analysis:
[`tests/eval/results/baseline_numbers.md`](tests/eval/results/baseline_numbers.md).

| Metric | Target | Best observed | Avg across 12 runs |
|---|---|---|---|
| Tickets evaluated | 50 | 50 | 49 |
| Intent accuracy | 100% | **100%** | 100% |
| Resolution accuracy | >70% | **68.8%** | ~57% |
| Escalation accuracy | -- | **85.7%** | ~82% |
| P95 latency | <60s | **51.0s** | ~56s |
| Cost per resolved ticket (est.) | <$0.05 | **$0.056** | $0.063 |
| QC rejection rate | -- | 40% | ~47% |

**The 70% target was not reliably crossed.** The best single run was 68.8%; the
average across 12 real-mode runs is ~57%. The 22-point spread between best (68.8%)
and worst (46.2%) run is driven almost entirely by LLM temperature variance in the
specialist and QC judge: the same ticket produces a different response each run,
which determines whether QC passes, which determines whether a retry happens, which
determines the final quality. This is not noise to report over; it is the real
behavior of a v1 prompt-based pipeline at this scale.

**What was fixed (deterministic improvements):**
- Order mock data: 25 entries corrected for product names; 3 entries corrected for
  payment methods (ORD-1003, ORD-1016, ORD-1025). Mismatches were causing guaranteed
  failures where the specialist correctly reported mock data that contradicted the spec.
- Stage 1.5 compliance checklist: deterministic check added between QC hard-rules and
  the LLM judge. Fires for refund approvals and verifies all four Section 8 disclosures
  (photo docs, RMA in 1 business day, 10-day ship-back, 2-day inspection) are present.
  If any are missing, returns targeted retry feedback rather than vague LLM feedback.
  This is the standard pattern for legally-required disclosure in production support.
- Judge token budget: QC judge raised from 512 to 800 tokens; eval harness judge from
  512 to 1024 tokens. Notes were being truncated before reaching the failing criterion.
- JudgeVerdict schema: `notes` field made optional; Haiku occasionally omits it and
  was silently dropping tickets as errors.

**What would reliably close the gap in production:**
- Structured specialist output with mandatory fields (`eligibility`, `policy_section`,
  `process_steps`, `payment_method`). Required disclosures become schema constraints,
  not prompt suggestions.
- Semantic response caching keyed on (intent, retrieved chunks, order facts). Same
  scenario always produces the same response; eliminates variance entirely.
- Fine-tuning on approved response examples.

---

## Running locally

### One-command start (Docker)

**Requirements:** Docker Desktop, an `.env` file with your API keys.

```bash
cp .env.example .env
# Fill in ANTHROPIC_API_KEY, VOYAGE_API_KEY, LANGCHAIN_API_KEY

./scripts/start_local.sh
```

On first run, migrate the database and ingest the knowledge base (one-time setup):

```bash
docker compose -f docker/docker-compose.yml exec api alembic upgrade head
docker compose -f docker/docker-compose.yml exec api python scripts/ingest_docs.py
```

Open http://localhost:8501 for the demo UI. The API is at http://localhost:8000. Worker metrics are at http://localhost:9091/metrics.

### Manual setup (for development)

**Requirements:** Python 3.13, Docker Desktop.

```bash
# 1. Start infrastructure (Postgres on 5433, Redis on 6379)
docker compose -f docker/docker-compose.yml up postgres redis -d

# 2. Install
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pre-commit install

# 3. Configure
cp .env.example .env
# Fill in ANTHROPIC_API_KEY, VOYAGE_API_KEY, LANGCHAIN_API_KEY

# 4. Apply database migrations
alembic upgrade head

# 5. Ingest knowledge base into pgvector
python scripts/ingest_docs.py

# 6. API smoke test
python scripts/hello_claude.py

# 7. Day 2 pipeline smoke test (three refund tickets, verifies retrieval + tools)
python scripts/test_day2_pipeline.py

# 8. Day 3 full-pipeline verification (happy path, QC retry, low-confidence escalation)
python scripts/test_day3_pipeline.py
```

> **Voyage AI free tier:** the ingestion script sleeps 21 seconds between files to respect the 3 RPM limit. Add a payment method at dash.voyageai.com/billing to unlock higher limits (200M free tokens still apply).

---

## Architecture decisions

**Why LangGraph over plain function calls?** Explicit state and conditional edges make the routing logic auditable. When a Quality Checker rejects a response, the retry path is a declared edge in the graph, not a hidden loop inside a function.

**Why separate Specialists instead of one general agent?** Different intent categories have different retrieval corpora, different tool sets, and different prompt constraints. Separating them keeps each agent's prompt tight and lets us measure per-category accuracy independently.

**Why Haiku for Router and Quality Checker?** Classification and rule-checking don't need Sonnet-level reasoning. Running Haiku at the bookends cuts cost significantly while keeping the expensive Sonnet calls focused on the actual response generation.

**Why pgvector over a managed vector DB?** At ~10k documents and one team, operating a separate vector service adds complexity without benefit. pgvector runs in the same Postgres instance as the rest of the application state. Migration path to Pinecone or Weaviate is documented if the doc count grows.

**Why async ingress?** LLM calls take 3-8 seconds. Holding an HTTP connection open for that duration at scale is a problem. The API accepts a ticket and returns a `ticket_id` immediately; the client polls or receives a webhook when processing completes.

**Why a QC retry cycle instead of just escalating on first rejection?** Most QC failures are recoverable: a response was too short, used a cop-out phrase, or scored just below the LLM judge threshold. A single retry with the rejection reason prepended to the prompt resolves these in practice. Escalating on the first rejection would push too many tickets to humans. Two rejections in a row signals a harder problem and escalates reliably.

**Why a cross-provider fallback to OpenAI?** Anthropic's API is occasionally unavailable (5xx, timeouts, rate limits). Switching to `gpt-4o-mini` mid-conversation rather than failing the ticket keeps SLAs intact. The Anthropic message format is converted to OpenAI's on the fly; the `provider` field in state records which backend actually generated the draft.

**Why a per-tool circuit breaker?** Downstream tools (order lookup, account status) can degrade independently. Without a circuit breaker, a slow or erroring tool adds latency to every ticket that triggers it. After five failures the circuit opens for 60 seconds; during that window the model receives an explicit "service unavailable" message and answers from policy alone rather than fabricating data. Redis `INCR` is atomic so there are no race conditions under concurrent load.
