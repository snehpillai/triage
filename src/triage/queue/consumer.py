"""Redis Streams consumer for the ticket processing pipeline.

Processing contract
-------------------
Happy path:
  XREADGROUP -> mark processing -> run LangGraph -> persist result -> XACK

Failure path:
  Unhandled exception -> mark failed -> log full traceback -> do NOT XACK
  Message stays in the PEL so the next startup can recover it.

PEL recovery (on startup):
  For each message in this consumer's PEL, fetch the ticket from DB.
  If status is still 'processing' the previous worker crashed mid-ticket.
  Escalate the ticket, write an EscalationRecord, then XACK.
  If status is anything else, the ticket was already handled - just XACK.
"""

import json
import os
import signal
import socket
import traceback
import uuid
from datetime import UTC, datetime
from typing import Any

import redis
from loguru import logger
from redis.exceptions import ResponseError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from triage.config import settings
from triage.db.models import EscalationRecord, Ticket, TicketStatus
from triage.graph.builder import app as _graph

_GROUP = "triage-workers"
_BLOCK_MS = 5_000  # wait up to 5 s for new messages per XREADGROUP call
_PEL_SCAN_LIMIT = 100  # max PEL entries to inspect on startup
_CRASH_REASON = "Worker crashed mid-processing, requires manual review"


class TicketConsumer:
    """Pulls messages from the Redis stream and runs the LangGraph pipeline."""

    def __init__(self) -> None:
        self._redis = redis.from_url(settings.redis_url, decode_responses=True)
        self._engine = create_engine(settings.database_url)
        self._stream = settings.redis_stream_name
        self._consumer = os.environ.get("WORKER_NAME") or socket.gethostname()
        self._running = True

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Start the consumer loop. Blocks until stopped via stop() or SIGTERM."""
        signal.signal(signal.SIGTERM, self._handle_signal)

        logger.info(
            "Worker starting: consumer={c} stream={s} group={g}",
            c=self._consumer,
            s=self._stream,
            g=_GROUP,
        )
        self._ensure_group()
        self._recover_pel()

        while self._running:
            try:
                results = self._redis.xreadgroup(
                    _GROUP,
                    self._consumer,
                    {self._stream: ">"},
                    count=1,
                    block=_BLOCK_MS,
                )
            except KeyboardInterrupt:
                logger.info("KeyboardInterrupt, stopping worker")
                break
            except Exception as exc:
                logger.error("XREADGROUP error: {e}", e=exc)
                continue

            if not results:
                continue

            for _stream_name, entries in results:
                for msg_id, fields in entries:
                    self._process(msg_id, fields)

        logger.info("Worker stopped: consumer={c}", c=self._consumer)

    def stop(self) -> None:
        """Signal the run loop to exit after the current message (or block timeout)."""
        self._running = False

    # ------------------------------------------------------------------
    # Startup helpers
    # ------------------------------------------------------------------

    def _handle_signal(self, signum: int, _frame: object) -> None:
        logger.info("Signal {s} received, stopping after current message", s=signum)
        self._running = False

    def _ensure_group(self) -> None:
        """Create the consumer group and stream if they do not already exist."""
        try:
            self._redis.xgroup_create(self._stream, _GROUP, id="0", mkstream=True)
            logger.info("Consumer group created: {g}", g=_GROUP)
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise
            # Group already exists - normal on restart.

    def _recover_pel(self) -> None:
        """Handle messages left in this consumer's PEL from a previous crash."""
        try:
            pending: list[dict[str, Any]] = self._redis.xpending_range(
                self._stream,
                _GROUP,
                min="-",
                max="+",
                count=_PEL_SCAN_LIMIT,
                consumername=self._consumer,
            )
        except Exception as exc:
            logger.warning("PEL query failed: {e}", e=exc)
            return

        if not pending:
            return

        logger.info("PEL recovery: {n} pending message(s) found", n=len(pending))
        for entry in pending:
            self._recover_one(entry["message_id"])

    def _recover_one(self, msg_id: str) -> None:
        """Escalate a ticket that was stuck processing when the previous worker crashed."""
        # Retrieve the original message from the stream to get the ticket_id.
        try:
            msgs = self._redis.xrange(self._stream, min=msg_id, max=msg_id, count=1)
        except Exception as exc:
            logger.error("XRANGE failed for PEL msg {id}: {e}", id=msg_id, e=exc)
            return

        if not msgs:
            # Message was trimmed from the stream - nothing to do.
            self._safe_xack(msg_id)
            return

        _, fields = msgs[0]
        ticket_id_str = fields.get("ticket_id", "")

        try:
            tid = uuid.UUID(ticket_id_str)
        except ValueError:
            logger.warning("PEL msg {id}: invalid ticket_id={v}", id=msg_id, v=ticket_id_str)
            self._safe_xack(msg_id)
            return

        with Session(self._engine) as session:
            ticket = session.get(Ticket, tid)

            if ticket is None or ticket.status != TicketStatus.processing:
                # Ticket was already handled (resolved/escalated/failed) or deleted.
                self._safe_xack(msg_id)
                return

            logger.warning(
                "PEL recovery: ticket={id} still in processing - previous worker crashed",
                id=ticket_id_str,
            )
            ticket.status = TicketStatus.escalated
            ticket.resolved_at = datetime.now(UTC)
            session.add(
                EscalationRecord(
                    ticket_id=ticket.id,
                    reason=_CRASH_REASON,
                    confidence_score=None,
                    context_summary=json.dumps(
                        {"escalation_reason": "worker_crash", "message_id": msg_id}
                    ),
                )
            )
            session.commit()

        self._safe_xack(msg_id)
        logger.info("PEL recovery complete: ticket={id} escalated", id=ticket_id_str)

    # ------------------------------------------------------------------
    # Per-message processing
    # ------------------------------------------------------------------

    def _process(self, msg_id: str, fields: dict[str, str]) -> None:
        """Process one stream message: load ticket, run pipeline, persist, ACK."""
        ticket_id_str = fields.get("ticket_id", "")
        content = fields.get("content", "")

        logger.info("Processing: ticket={id} msg={msg}", id=ticket_id_str, msg=msg_id)

        try:
            tid = uuid.UUID(ticket_id_str)
        except ValueError:
            logger.error("Invalid ticket_id in msg {msg}: {v}", msg=msg_id, v=ticket_id_str)
            self._safe_xack(msg_id)
            return

        if not self._set_status(tid, TicketStatus.processing):
            # Ticket not found in DB - ACK so the message doesn't block the queue.
            self._safe_xack(msg_id)
            return

        try:
            state = _graph.invoke({"ticket_id": ticket_id_str, "content": content})
            self._persist_result(tid, state)
            self._safe_xack(msg_id)
            logger.info(
                "Ticket done: id={id} escalated={esc}",
                id=ticket_id_str,
                esc=state.get("escalate", False),
            )
        except Exception:
            logger.error(
                "Unhandled exception for ticket={id}:\n{tb}",
                id=ticket_id_str,
                tb=traceback.format_exc(),
            )
            self._set_status(tid, TicketStatus.failed)
            # Do NOT XACK: message stays in PEL so the next startup can recover it.

    # ------------------------------------------------------------------
    # DB helpers
    # ------------------------------------------------------------------

    def _set_status(self, ticket_id: uuid.UUID, status: TicketStatus) -> bool:
        """Update ticket.status. Returns False if the ticket row does not exist."""
        with Session(self._engine) as session:
            ticket = session.get(Ticket, ticket_id)
            if ticket is None:
                logger.warning("Ticket not found: {id}", id=ticket_id)
                return False
            ticket.status = status
            session.commit()
        return True

    def _persist_result(self, ticket_id: uuid.UUID, state: dict[str, Any]) -> None:
        """Write the completed graph state back to the Ticket row.

        The escalator node already wrote an EscalationRecord if escalate=True,
        so we only update the Ticket here.
        """
        escalated: bool = state.get("escalate", False)
        with Session(self._engine) as session:
            ticket = session.get(Ticket, ticket_id)
            if ticket is None:
                logger.error("Ticket vanished before result could be saved: {id}", id=ticket_id)
                return
            ticket.status = TicketStatus.escalated if escalated else TicketStatus.resolved
            ticket.response = state.get("final_response") or ""
            ticket.intent = state.get("intent")
            ticket.resolved_at = datetime.now(UTC)
            session.commit()

    # ------------------------------------------------------------------
    # Redis helpers
    # ------------------------------------------------------------------

    def _safe_xack(self, msg_id: str) -> None:
        """XACK, logging but not raising on failure."""
        try:
            self._redis.xack(self._stream, _GROUP, msg_id)
        except Exception as exc:
            logger.error("XACK failed for msg {id}: {e}", id=msg_id, e=exc)
