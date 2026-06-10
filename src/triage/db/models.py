import enum
import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class TicketStatus(enum.StrEnum):
    pending = "pending"
    processing = "processing"
    resolved = "resolved"
    escalated = "escalated"
    failed = "failed"


class Ticket(Base):
    __tablename__ = "tickets"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    content: Mapped[str] = mapped_column(Text)
    intent: Mapped[str | None] = mapped_column(String(50), nullable=True)
    status: Mapped[TicketStatus] = mapped_column(
        SAEnum(TicketStatus, name="ticket_status"), default=TicketStatus.pending
    )
    response: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    escalation_records: Mapped[list["EscalationRecord"]] = relationship(
        back_populates="ticket", cascade="all, delete-orphan"
    )


class DocumentChunk(Base):
    __tablename__ = "document_chunks"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    source_file: Mapped[str] = mapped_column(String(255))
    chunk_index: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(Vector(1024))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EscalationRecord(Base):
    __tablename__ = "escalation_records"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    ticket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tickets.id"), nullable=False)
    reason: Mapped[str] = mapped_column(String(500))
    confidence_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    context_summary: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    ticket: Mapped["Ticket"] = relationship(back_populates="escalation_records")
