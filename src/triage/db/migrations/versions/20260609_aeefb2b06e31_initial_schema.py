"""initial_schema

Revision ID: aeefb2b06e31
Revises:
Create Date: 2026-06-09 18:13:54.225232

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic
revision: str = "aeefb2b06e31"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. pgvector extension - must exist before any Vector column is created
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # 2. Postgres ENUM type via raw SQL - must exist before the tickets table.
    # Using op.execute() rather than postgresql.ENUM.create() so SQLAlchemy's
    # event system cannot attempt a second CREATE TYPE when op.create_table() fires.
    op.execute(
        "CREATE TYPE ticket_status AS ENUM "
        "('pending', 'processing', 'resolved', 'escalated', 'failed')"
    )

    # 3. tickets - no foreign key dependencies, create first
    op.create_table(
        "tickets",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("intent", sa.String(50), nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM(
                "pending",
                "processing",
                "resolved",
                "escalated",
                "failed",
                name="ticket_status",
                create_type=False,  # type already created above, do not re-emit
            ),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("response", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    # 4. document_chunks - no foreign key dependencies
    op.create_table(
        "document_chunks",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("source_file", sa.String(255), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(1024), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # 5. escalation_records - depends on tickets via FK
    op.create_table(
        "escalation_records",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("ticket_id", sa.UUID(), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("confidence_score", sa.Float(), nullable=True),
        sa.Column("context_summary", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    # 6. IVFFlat index on document_chunks.embedding - must be after the table and
    # after the column is cast to vector type. Uses cosine distance (<=>) for
    # semantic similarity search. lists=100 balances recall vs query speed at our scale.
    op.execute(
        "CREATE INDEX document_chunks_embedding_ivfflat_idx "
        "ON document_chunks "
        "USING ivfflat (embedding vector_cosine_ops) "
        "WITH (lists = 100)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS document_chunks_embedding_ivfflat_idx")
    op.drop_table("escalation_records")
    op.drop_table("document_chunks")
    op.drop_table("tickets")

    ticket_status_enum = postgresql.ENUM(name="ticket_status")
    ticket_status_enum.drop(op.get_bind(), checkfirst=True)

    # Intentionally not dropping the vector extension -
    # it may be used by other schemas and dropping extensions has wide impact
