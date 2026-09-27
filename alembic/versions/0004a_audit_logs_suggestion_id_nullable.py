"""audit_logs: make suggestion_id nullable

Revision ID: 0004a
Revises: 0003
Create Date: 2026-09-26
"""
import sqlalchemy as sa
from alembic import op

revision = "0004a"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "audit_logs",
        "suggestion_id",
        existing_type=sa.UUID(),
        nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "audit_logs",
        "suggestion_id",
        existing_type=sa.UUID(),
        nullable=False,
    )
