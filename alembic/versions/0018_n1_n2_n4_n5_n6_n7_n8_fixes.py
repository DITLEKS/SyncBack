"""N-1..N-8 schema fixes

Revision ID: 0018
Revises: 0017
Create Date: 2026-09-27

Изменения:
  N-2: ADD COLUMN exported_storage_key VARCHAR(1024) NULL в documents.
  N-4: DROP INDEX CONCURRENTLY ix_document_blocks_document_id (одиночный — покрыт составным).
  N-5: DROP INDEX CONCURRENTLY ix_suggestions_analysis_job_id (покрыт ix_suggestions_job_status).
  N-6: DROP INDEX CONCURRENTLY ix_suggestions_document_id (покрыт ix_suggestions_document_status).

N-1, N-3, N-7, N-8 — правки только в Python-коде, миграция не нужна.
"""
from alembic import op
import sqlalchemy as sa

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # N-2: добавляем поле exported_storage_key в documents.
    op.add_column(
        "documents",
        sa.Column("exported_storage_key", sa.String(1024), nullable=True),
    )

    # N-4: удаляем одиночный индекс document_blocks.document_id.
    # ix_document_blocks_doc_position (document_id, position) покрывает все запросы.
    op.execute(
        "DROP INDEX CONCURRENTLY IF EXISTS ix_document_blocks_document_id"
    )

    # N-5: удаляем одиночный индекс suggestions.analysis_job_id.
    # ix_suggestions_job_status (analysis_job_id, status) покрывает все запросы.
    op.execute(
        "DROP INDEX CONCURRENTLY IF EXISTS ix_suggestions_analysis_job_id"
    )

    # N-6: удаляем одиночный индекс suggestions.document_id.
    # ix_suggestions_document_status (document_id, status) покрывает все запросы.
    op.execute(
        "DROP INDEX CONCURRENTLY IF EXISTS ix_suggestions_document_id"
    )


def downgrade() -> None:
    # N-2: удаляем добавленное поле.
    op.drop_column("documents", "exported_storage_key")

    # Восстанавливаем одиночные индексы.
    op.create_index("ix_document_blocks_document_id", "document_blocks", ["document_id"])
    op.create_index("ix_suggestions_analysis_job_id", "suggestions", ["analysis_job_id"])
    op.create_index("ix_suggestions_document_id", "suggestions", ["document_id"])
