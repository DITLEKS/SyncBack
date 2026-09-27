"""
R-1..R-6: schema cleanup

Revision ID: 0017
Downgrade: supported (rollback restores all dropped columns/indexes).

R-1  Drop documents.current_analysis_job_id (circular FK, never read).
     Add partial index ix_analysis_jobs_doc_latest for fast "latest job" lookup.
R-2  Add suggestions.document_id (denorm, avoids JOIN through analysis_jobs).
     Add composite index ix_suggestions_document_status.
R-3  Drop document_blocks.raw_markdown (duplicated content for md-docs).
R-4  Drop sources.uploaded_at (duplicated created_at).
R-5  Drop index ix_audit_logs_document_id (covered by ix_audit_logs_document_created).
R-6  Drop index ix_analysis_jobs_idempotency_key (covered by uq_analysis_jobs_doc_idem_key).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------ R-1
    op.drop_constraint(
        "documents_current_analysis_job_id_fkey",
        "documents",
        type_="foreignkey",
    )
    op.drop_column("documents", "current_analysis_job_id")
    # Partial index: подбирает последний завершённый job без seq-scan.
    op.execute(
        """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_analysis_jobs_doc_latest
        ON analysis_jobs (document_id, created_at DESC)
        """
    )

    # ------------------------------------------------------------------ R-2
    op.add_column(
        "suggestions",
        sa.Column(
            "document_id",
            UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=True,   # nullable во время заполнения backfill
        ),
    )
    # Backfill: заполнить document_id из родительского AnalysisJob.
    op.execute(
        """
        UPDATE suggestions s
        SET document_id = aj.document_id
        FROM analysis_jobs aj
        WHERE s.analysis_job_id = aj.id
        """
    )
    # Превращаем в NOT NULL после backfill.
    op.alter_column("suggestions", "document_id", nullable=False)
    op.execute(
        """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_suggestions_document_status
        ON suggestions (document_id, status)
        """
    )

    # ------------------------------------------------------------------ R-3
    op.drop_column("document_blocks", "raw_markdown")

    # ------------------------------------------------------------------ R-4
    op.drop_column("sources", "uploaded_at")

    # ------------------------------------------------------------------ R-5
    op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_audit_logs_document_id")

    # ------------------------------------------------------------------ R-6
    op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_analysis_jobs_idempotency_key")


def downgrade() -> None:
    # R-6
    op.create_index("ix_analysis_jobs_idempotency_key", "analysis_jobs", ["idempotency_key"])
    # R-5
    op.create_index("ix_audit_logs_document_id", "audit_logs", ["document_id"])
    # R-4
    op.add_column(
        "sources",
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=True),
    )
    # R-3
    op.add_column(
        "document_blocks",
        sa.Column("raw_markdown", sa.Text, nullable=True),
    )
    # R-2
    op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_suggestions_document_status")
    op.drop_constraint("suggestions_document_id_fkey", "suggestions", type_="foreignkey")
    op.drop_column("suggestions", "document_id")
    # R-1
    op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_analysis_jobs_doc_latest")
    op.add_column(
        "documents",
        sa.Column(
            "current_analysis_job_id",
            UUID(as_uuid=True),
            sa.ForeignKey("analysis_jobs.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
