"""add debug_info column to tickets

Revision ID: b8c3f2a14e90
Revises: aeefb2b06e31
Create Date: 2026-06-22 20:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b8c3f2a14e90"
down_revision: str | None = "aeefb2b06e31"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tickets", sa.Column("debug_info", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("tickets", "debug_info")
