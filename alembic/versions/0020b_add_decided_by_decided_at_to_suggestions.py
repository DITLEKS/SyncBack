"""C-1 (issue #37): add decided_by, decided_at to suggestions.

Revision ID: 0020b
Revises: 0020a
Create Date: 2026-09-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0020b"
down_revision = "0020a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Колонки есть в 0001; миграция пропускается на базах, созданных с нуля,
    # и добавляет их только там, где начальная схема была старее.
    existing = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("suggestions")}
    if {"decided_by", "decided_at"} <= existing:
        return
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
    # Колонки принадлежат 0001, здесь ничего не удаляем.
    pass
