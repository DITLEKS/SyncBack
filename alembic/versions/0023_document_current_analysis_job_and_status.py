"""Add documents.current_analysis_job_id and extend document_status enum.

Revision ID: 0023
Revises: 0022
Create Date: 2026-09-27

Changes:
  1. documents.current_analysis_job_id — UUID nullable FK -> analysis_jobs(id)
     ON DELETE SET NULL. Used for fast access to the current job without JOIN.
  2. ix_documents_current_analysis_job_id — partial index for reverse lookup.
  3. document_status enum: adds 'error' and 'cancelled' (IF NOT EXISTS — safe
     to apply even if values were added by an earlier migration).

downgrade: DROP COLUMN, DROP INDEX, and safe-remove new enum values via
     recreate (PostgreSQL does not support DROP VALUE for enum).
     downgrade() restores 'ready' (not 'approved') to match app status.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Extend enum (idempotent — IF NOT EXISTS)
    op.execute("ALTER TYPE document_status ADD VALUE IF NOT EXISTS 'error'")
    op.execute("ALTER TYPE document_status ADD VALUE IF NOT EXISTS 'cancelled'")

    # 2. Add column to documents
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

    # 3. Partial index: reverse lookup job -> document
    op.create_index(
        "ix_documents_current_analysis_job_id",
        "documents",
        ["current_analysis_job_id"],
        postgresql_where=sa.text("current_analysis_job_id IS NOT NULL"),
    )


def downgrade() -> None:
    # 1. Drop index and column
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

    # 2. Remove 'error'/'cancelled' from enum via recreate.
    #    Rows with these statuses are reset to 'draft' first.
    op.execute(
        """
        UPDATE documents
        SET status = 'draft'
        WHERE status IN ('error', 'cancelled')
        """
    )
    op.execute(
        """
        ALTER TYPE document_status RENAME TO document_status_old;
        CREATE TYPE document_status AS ENUM (
            'draft', 'in_progress', 'awaiting_approval', 'ready'
        );
        ALTER TABLE documents
            ALTER COLUMN status TYPE document_status
            USING status::text::document_status;
        DROP TYPE document_status_old;
        """
    )
