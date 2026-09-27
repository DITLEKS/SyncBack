"""add download to audit_action enum

Revision ID: 0002a
Revises: 0001
Create Date: 2026-09-26
"""
from alembic import op

revision = "0002a"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE audit_action ADD VALUE IF NOT EXISTS 'download'")


def downgrade() -> None:
    pass
