"""
R-1..R-6: schema cleanup

Revision ID: 0017a
Revises: 0016

R-1  Drop documents.current_analysis_job_id (circular FK, never read).
     Add plain index ix_analysis_jobs_doc_latest for fast "latest job" lookup.
R-2  Add suggestions.document_id (denorm, avoids JOIN through analysis_jobs).
     Add composite index ix_suggestions_document_status.
R-3  Drop document_blocks.raw_markdown (duplicated content for md-docs).
R-4  Rename sources.uploaded_at → created_at (как в ORM-модели; отдельной created_at в БД не было).
R-5  Drop index ix_audit_logs_document_id.
R-6  Drop index ix_analysis_jobs_idempotency_key.

Note: CREATE/DROP INDEX CONCURRENTLY cannot run inside a transaction.
All indexes here use plain CREATE/DROP INDEX.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0017a"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---- R-1
    op.drop_constraint(
        "documents_current_analysis_job_id_fkey",
        "documents",
        type_="foreignkey",
    )
    op.drop_column("documents", "current_analysis_job_id")
    op.create_index(
        "ix_analysis_jobs_doc_latest",
        "analysis_jobs",
        ["document_id", "created_at"],
    )

    # ---- R-2
    op.add_column(
        "suggestions",
        sa.Column(
            "document_id",
            UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.execute(
        """
        UPDATE suggestions s
        SET document_id = aj.document_id
        FROM analysis_jobs aj
        WHERE s.analysis_job_id = aj.id
        """
    )
    op.alter_column("suggestions", "document_id", nullable=False)
    op.create_index(
        "ix_suggestions_document_status",
        "suggestions",
        ["document_id", "status"],
    )

    # ---- R-3
    op.drop_column("document_blocks", "raw_markdown")

    # ---- R-4
    op.execute("UPDATE sources SET uploaded_at = now() WHERE uploaded_at IS NULL")
    op.alter_column("sources", "uploaded_at", new_column_name="created_at", nullable=False)

    # ---- R-5
    op.drop_index("ix_audit_logs_document_id", table_name="audit_logs")

    # ---- R-6
    op.drop_index("ix_analysis_jobs_idempotency_key", table_name="analysis_jobs")


def downgrade() -> None:
    # R-6
    op.create_index("ix_analysis_jobs_idempotency_key", "analysis_jobs", ["idempotency_key"])
    # R-5
    op.create_index("ix_audit_logs_document_id", "audit_logs", ["document_id"])
    # R-4
    op.alter_column("sources", "created_at", new_column_name="uploaded_at", nullable=True)
    # R-3
    op.add_column(
        "document_blocks",
        sa.Column("raw_markdown", sa.Text, nullable=True),
    )
    # R-2
    op.drop_index("ix_suggestions_document_status", table_name="suggestions")
    op.drop_constraint("suggestions_document_id_fkey", "suggestions", type_="foreignkey")
    op.drop_column("suggestions", "document_id")
    # R-1
    op.drop_index("ix_analysis_jobs_doc_latest", table_name="analysis_jobs")
    op.add_column(
        "documents",
        sa.Column(
            "current_analysis_job_id",
            UUID(as_uuid=True),
            sa.ForeignKey("analysis_jobs.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
