"""Ticket ingress and status endpoints."""

import asyncio
import json
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any, Literal

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Header, HTTPException
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from triage.config import settings
from triage.db.models import Ticket
from triage.db.models import TicketStatus as _DBStatus
from triage.observability.metrics import record_ticket_latency, record_ticket_outcome

# ---------------------------------------------------------------------------
# Async DB session
# ---------------------------------------------------------------------------

# psycopg3 async dialect: swap the driver name in the URL.
_db_url = settings.database_url
if "+psycopg_async" not in _db_url:
    _db_url = _db_url.replace("postgresql+psycopg://", "postgresql+psycopg_async://", 1)

_engine = create_async_engine(_db_url, pool_pre_ping=True)
_AsyncSession = async_sessionmaker(_engine, expire_on_commit=False)


async def get_db() -> AsyncGenerator[AsyncSession]:
    async with _AsyncSession() as session:
        yield session


# ---------------------------------------------------------------------------
# Async Redis client (module-level pool, shared across requests)
# ---------------------------------------------------------------------------

_redis: aioredis.Redis = aioredis.from_url(settings.redis_url, decode_responses=True)


async def get_redis() -> aioredis.Redis:
    return _redis


# ---------------------------------------------------------------------------
# API key auth
# ---------------------------------------------------------------------------


async def verify_api_key(x_api_key: str | None = Header(None, alias="X-API-Key")) -> None:
    """Reject requests without a valid key unless running in development mode."""
    if settings.environment == "development":
        return
    if not x_api_key or x_api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class TicketRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=5000, description="Raw ticket text")
    customer_id: str | None = Field(None, description="Optional customer identifier")


class TicketCreatedResponse(BaseModel):
    ticket_id: str
    status: Literal["pending"]


class TicketDebugInfo(BaseModel):
    confidence: float | None = None
    context_docs: list[dict[str, Any]] = []
    tool_results: dict[str, Any] = {}
    qc_score: float | None = None
    qc_feedback: str | None = None
    qc_passed: bool | None = None
    retry_count: int = 0
    provider: str | None = None


class TicketStatusResponse(BaseModel):
    ticket_id: str
    status: str
    intent: str | None
    response: str | None
    escalated: bool
    escalation_reason: str | None
    created_at: datetime
    resolved_at: datetime | None
    debug_info: TicketDebugInfo | None = None


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/tickets", tags=["tickets"])


# ---------------------------------------------------------------------------
# Sync-mode pipeline helper
# ---------------------------------------------------------------------------


def _build_debug_info_json(state: dict[str, Any]) -> str:
    """Serialize pipeline debug state to JSON. Used in sync mode."""
    context_docs = []
    for doc in state.get("context_docs") or []:
        try:
            context_docs.append(
                {
                    "source_file": doc.chunk.source_file,
                    "score": round(doc.score, 4),
                    "content": doc.chunk.content[:600],
                }
            )
        except Exception:
            pass

    tool_results: dict[str, Any] = {}
    for name, result in (state.get("tool_results") or {}).items():
        try:
            tool_results[name] = (
                result.model_dump() if hasattr(result, "model_dump") else str(result)
            )
        except Exception:
            tool_results[name] = "<unserializable>"

    return json.dumps(
        {
            "confidence": state.get("confidence"),
            "context_docs": context_docs,
            "tool_results": tool_results,
            "qc_score": state.get("qc_score"),
            "qc_feedback": state.get("qc_feedback"),
            "qc_passed": state.get("qc_passed"),
            "retry_count": state.get("retry_count", 0),
            "provider": state.get("provider"),
        }
    )


def _run_pipeline_and_persist(ticket_id_str: str, content: str) -> None:
    """Invoke the LangGraph pipeline synchronously and persist the result.

    Designed to run inside asyncio.to_thread() so the FastAPI event loop
    is not blocked during the 8-15 second pipeline execution.
    """
    import time

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from triage.graph.builder import app as _graph

    start = time.monotonic()
    try:
        state = _graph.invoke({"ticket_id": ticket_id_str, "content": content})
    except Exception as exc:
        logger.error("Sync pipeline failed for ticket={id}: {e}", id=ticket_id_str, e=exc)
        engine = create_engine(settings.database_url)
        with Session(engine) as session:
            ticket = session.get(Ticket, uuid.UUID(ticket_id_str))
            if ticket:
                ticket.status = _DBStatus.failed
                ticket.resolved_at = datetime.now(UTC)
                session.commit()
        return

    elapsed = time.monotonic() - start
    escalated: bool = state.get("escalate", False)
    intent: str = state.get("intent", "unknown")

    engine = create_engine(settings.database_url)
    with Session(engine) as session:
        ticket = session.get(Ticket, uuid.UUID(ticket_id_str))
        if ticket:
            ticket.status = _DBStatus.escalated if escalated else _DBStatus.resolved
            ticket.response = state.get("final_response") or ""
            ticket.intent = intent
            ticket.resolved_at = datetime.now(UTC)
            ticket.debug_info = _build_debug_info_json(state)
            session.commit()

    record_ticket_outcome(intent, "escalated" if escalated else "resolved")
    record_ticket_latency(intent, elapsed)


@router.post(
    "",
    response_model=TicketCreatedResponse,
    status_code=202,
    dependencies=[Depends(verify_api_key)],
    summary="Submit a support ticket",
)
async def create_ticket(
    body: TicketRequest,
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
) -> TicketCreatedResponse:
    """Persist the ticket and enqueue it for processing.

    Commits the DB row first, then publishes to the Redis stream. Returns 202
    once both succeed. If the stream publish fails, the ticket row exists in
    Postgres (status=pending) but is not yet queued; returns 503 in that case.
    """
    # Phase 1: write to DB and commit.
    async with db.begin():
        ticket = Ticket(content=body.content, status=_DBStatus.pending)
        db.add(ticket)
        await db.flush()  # assigns ticket.id; still within the transaction
        ticket_id = str(ticket.id)
    # Transaction committed. ticket_id is now a durable UUID in Postgres.

    if settings.sync_mode:
        # Run the pipeline inline. Blocks the request for 8-15 seconds but
        # requires no worker process - correct for a single-URL demo deployment.
        logger.info("Sync mode: running pipeline inline for ticket={id}", id=ticket_id)
        await asyncio.to_thread(_run_pipeline_and_persist, ticket_id, body.content)
        logger.info("Sync mode: pipeline complete for ticket={id}", id=ticket_id)
        return TicketCreatedResponse(ticket_id=ticket_id, status="pending")

    # Phase 2 (async mode): publish to Redis stream.
    try:
        await redis.xadd(
            settings.redis_stream_name,
            {"ticket_id": ticket_id, "content": body.content},
        )
    except Exception as exc:
        logger.error("Redis XADD failed for ticket={id}: {e}", id=ticket_id, e=exc)
        raise HTTPException(
            status_code=503,
            detail="Queue temporarily unavailable. The ticket was saved; please retry.",
        ) from exc

    logger.info("Ticket accepted: id={id}", id=ticket_id)
    return TicketCreatedResponse(ticket_id=ticket_id, status="pending")


@router.get(
    "/{ticket_id}",
    response_model=TicketStatusResponse,
    dependencies=[Depends(verify_api_key)],
    summary="Get ticket status",
)
async def get_ticket(
    ticket_id: str,
    db: AsyncSession = Depends(get_db),
) -> TicketStatusResponse:
    """Return the current status and (when resolved) response for a ticket."""
    try:
        tid = uuid.UUID(ticket_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Ticket not found") from None

    stmt = select(Ticket).where(Ticket.id == tid).options(selectinload(Ticket.escalation_records))
    result = await db.execute(stmt)
    ticket = result.scalar_one_or_none()

    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")

    # Derive escalation fields from the EscalationRecord relationship rather
    # than from ticket.status, because the worker that updates status doesn't
    # exist yet; escalation_records are written by the graph regardless.
    escalated = bool(ticket.escalation_records)
    escalation_reason: str | None = None
    if ticket.escalation_records:
        latest = max(ticket.escalation_records, key=lambda r: r.created_at)
        escalation_reason = latest.reason

    debug_info: TicketDebugInfo | None = None
    if ticket.debug_info:
        try:
            debug_info = TicketDebugInfo(**json.loads(ticket.debug_info))
        except Exception:
            pass

    return TicketStatusResponse(
        ticket_id=str(ticket.id),
        status=str(ticket.status),
        intent=ticket.intent,
        response=ticket.response,
        escalated=escalated,
        escalation_reason=escalation_reason,
        created_at=ticket.created_at,
        resolved_at=ticket.resolved_at,
        debug_info=debug_info,
    )
