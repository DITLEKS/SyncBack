"""C-1 (issue #37): add decided_by, decided_at to suggestions.

Revision ID: 0020
Revises: 0019
Create Date: 2026-09-27
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "suggestions",
        sa.Column(
            "decided_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "suggestions",
        sa.Column(
            "decided_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("suggestions", "decided_at")
    op.drop_column("suggestions", "decided_by")
