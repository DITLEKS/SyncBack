"""N-1..N-8 schema fixes

Revision ID: 0018b
Revises: 0018a
Create Date: 2026-09-27

Changes:
  N-2: ADD COLUMN exported_storage_key VARCHAR(1024) NULL in documents.
  N-4: DROP INDEX ix_document_blocks_document_id (covered by composite).
  N-5: DROP INDEX ix_suggestions_analysis_job_id (covered by ix_suggestions_job_status).
  N-6: DROP INDEX ix_suggestions_document_id (covered by ix_suggestions_document_status).

N-1, N-3, N-7, N-8 are Python-only fixes; no schema migration needed.

Note: CONCURRENTLY removed — indexes dropped with plain DROP INDEX inside transaction.
"""
from alembic import op
import sqlalchemy as sa

revision = "0018b"
down_revision = "0018a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # N-2
    op.add_column(
        "documents",
        sa.Column("exported_storage_key", sa.String(1024), nullable=True),
    )

    # N-4
    op.drop_index("ix_document_blocks_document_id", table_name="document_blocks", if_exists=True)

    # N-5
    op.drop_index("ix_suggestions_analysis_job_id", table_name="suggestions", if_exists=True)

    # N-6
    op.drop_index("ix_suggestions_document_id", table_name="suggestions", if_exists=True)


def downgrade() -> None:
    op.drop_column("documents", "exported_storage_key")
    op.create_index("ix_document_blocks_document_id", "document_blocks", ["document_id"])
    op.create_index("ix_suggestions_analysis_job_id", "suggestions", ["analysis_job_id"])
    op.create_index("ix_suggestions_document_id", "suggestions", ["document_id"])
