"""Миграция 0016: OPT-4 + OPT-5

OPT-4: индекс на suggestions.document_id
  Используется в DashboardRepository.get_attention_documents /
  get_recent_documents при JOIN / EXISTS по document_id.

OPT-5: GIN-индекс pg_trgm на documents.name
  Позволяет ILIKE '%...%' использовать GIN-индекс вместо seq-scan.
  Требует расширение pg_trgm (включается в стандартный PostgreSQL,
  не требует отдельной установки).

Revision ID: 0016
Down revision: 0015
"""
import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # OPT-4: индекс на suggestions.document_id
    op.create_index(
        "ix_suggestions_document_id",
        "suggestions",
        ["document_id"],
    )

    # OPT-5: GIN-индекс для ILIKE-поиска по имени документа
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_documents_name_trgm
        ON documents USING gin (name gin_trgm_ops)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_documents_name_trgm")
    op.drop_index("ix_suggestions_document_id", table_name="suggestions")
