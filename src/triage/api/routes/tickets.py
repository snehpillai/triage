"""Ticket ingress and status endpoints."""

import uuid
from collections.abc import AsyncGenerator
from datetime import datetime
from typing import Literal

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


class TicketStatusResponse(BaseModel):
    ticket_id: str
    status: str
    response: str | None
    escalated: bool
    escalation_reason: str | None
    created_at: datetime
    resolved_at: datetime | None


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/tickets", tags=["tickets"])


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

    # Phase 2: publish to Redis stream.
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

    return TicketStatusResponse(
        ticket_id=str(ticket.id),
        status=str(ticket.status),
        response=ticket.response,
        escalated=escalated,
        escalation_reason=escalation_reason,
        created_at=ticket.created_at,
        resolved_at=ticket.resolved_at,
    )
