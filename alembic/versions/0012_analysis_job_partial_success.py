"""P0-7b: add 'partial_success' enum value AND boolean column to analysis_jobs.

The enum value must exist in the PostgreSQL type BEFORE any row can carry
that value, so both changes live in the same migration:
  1. ALTER TYPE analysis_job_status ADD VALUE 'partial_success'
  2. ALTER TABLE analysis_jobs ADD COLUMN partial_success BOOLEAN

On PostgreSQL 12+ ADD VALUE runs inside a transaction, protected by
IF NOT EXISTS for idempotency.

Revision ID: 0012
Revises:     0011
Create Date: 2026-09-20
"""

from alembic import op
import sqlalchemy as sa

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Extend the PostgreSQL enum type first
    op.execute(
        "ALTER TYPE analysis_job_status ADD VALUE IF NOT EXISTS 'partial_success'"
    )
    # 2. Now safe to add the boolean summary column
    op.add_column(
        "analysis_jobs",
        sa.Column(
            "partial_success",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("analysis_jobs", "partial_success")
    # PostgreSQL does not support removing enum values without recreating the type.
    # Leaving 'partial_success' in the type is safe: no rows will carry it after
    # this downgrade because the application code no longer references it.
