"""
P0-7: idempotency_key column + unique constraint on analysis_jobs,
      partial_success boolean column on analysis_jobs.

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-20
"""

from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- analysis_jobs: idempotency_key ---
    op.add_column(
        "analysis_jobs",
        sa.Column("idempotency_key", sa.String(128), nullable=True),
    )
    op.create_index(
        "ix_analysis_jobs_idempotency_key",
        "analysis_jobs",
        ["idempotency_key"],
        unique=False,
    )
    op.create_unique_constraint(
        "uq_analysis_jobs_doc_idem_key",
        "analysis_jobs",
        ["document_id", "idempotency_key"],
    )

    # --- analysis_jobs: partial_success ---
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
    op.drop_constraint("uq_analysis_jobs_doc_idem_key", "analysis_jobs", type_="unique")
    op.drop_index("ix_analysis_jobs_idempotency_key", table_name="analysis_jobs")
    op.drop_column("analysis_jobs", "idempotency_key")
    op.drop_column("analysis_jobs", "partial_success")
