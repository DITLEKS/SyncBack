"""Add documents.current_analysis_job_id.

Revision ID: 0023
Revises: 0022
Create Date: 2026-09-27

Changes:
  1. documents.current_analysis_job_id — UUID nullable FK -> analysis_jobs(id)
     ON DELETE SET NULL. Used for fast access to the current job without JOIN.
  2. ix_documents_current_analysis_job_id — partial index for reverse lookup.

Note: 'error' and 'cancelled' were added to document_status in 0006 and
removed again in 0020a. This migration no longer touches the enum.

downgrade: DROP COLUMN + DROP INDEX only.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add column to documents
    op.add_column(
        "documents",
        sa.Column(
            "current_analysis_job_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey(
                "analysis_jobs.id",
                name="fk_documents_current_analysis_job_id",
                ondelete="SET NULL",
            ),
            nullable=True,
        ),
    )

    # Partial index: reverse lookup job -> document
    op.create_index(
        "ix_documents_current_analysis_job_id",
        "documents",
        ["current_analysis_job_id"],
        postgresql_where=sa.text("current_analysis_job_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_documents_current_analysis_job_id",
        table_name="documents",
    )
    op.drop_constraint(
        "fk_documents_current_analysis_job_id",
        "documents",
        type_="foreignkey",
    )
    op.drop_column("documents", "current_analysis_job_id")
