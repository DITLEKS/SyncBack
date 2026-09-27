"""P2: index on suggestions.decided_by for filter/join queries.

Revision ID: 0022
Revises: 0021
Create Date: 2026-09-27

P2: индекс нужен для запросов вида:
  - «показать все правки, принятые/отклонённые конкретным пользователем»
  - JOIN users ON suggestions.decided_by = users.id
Partial WHERE decided_by IS NOT NULL исключает незакрытые правки (majority).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_suggestions_decided_by",
        "suggestions",
        ["decided_by"],
        postgresql_where=sa.text("decided_by IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_suggestions_decided_by", table_name="suggestions")
