"""add download audit action value

Revision ID: 0002_add_download_enum_value
Revises: 0001_initial_schema
Create Date: 2026-08-12
"""

from alembic import op

revision = "0002_add_download_enum_value"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE audit_action ADD VALUE IF NOT EXISTS 'download'")


def downgrade() -> None:
    # PostgreSQL does not support removing individual enum values safely.
    # Leaving the added value in place is the safest downgrade behavior.
    pass
