"""
P0-7a: idempotency_key column + unique constraint on analysis_jobs.

NOTE: partial_success was originally added here as a boolean column,
but the corresponding enum value 'partial_success' is only added to
analysis_job_status in migration 0012. Having the column before the
enum value causes a ProgrammingError on the first INSERT with that
value. partial_success column + enum value are now co-located in 0012.

Revision ID: 0007
Revises: 0006_document_status_lifecycle
Create Date: 2026-09-20
"""

from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006_document_status_lifecycle"
branch_labels = None
depends_on = None


def upgrade() -> None:
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


def downgrade() -> None:
    op.drop_constraint("uq_analysis_jobs_doc_idem_key", "analysis_jobs", type_="unique")
    op.drop_index("ix_analysis_jobs_idempotency_key", table_name="analysis_jobs")
    op.drop_column("analysis_jobs", "idempotency_key")
