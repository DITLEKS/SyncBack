"""Migration 0019: OPT-4 + OPT-5

OPT-4: index on suggestions.document_id
  Used in DashboardRepository.get_attention_documents /
  get_recent_documents via JOIN / EXISTS on document_id.

OPT-5: GIN pg_trgm index on documents.name
  Enables ILIKE '%...%' to use GIN index instead of seq-scan.
  Requires pg_trgm extension (ships with standard PostgreSQL).

Note: CONCURRENTLY removed — index created with plain CREATE INDEX inside
transaction. For large production tables, apply manually with CONCURRENTLY
outside Alembic.

Revision ID: 0019
Down revision: 0018b
"""
import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # OPT-4
    op.create_index(
        "ix_suggestions_document_id",
        "suggestions",
        ["document_id"],
    )

    # OPT-5
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_documents_name_trgm
        ON documents USING gin (name gin_trgm_ops)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_documents_name_trgm")
    op.drop_index("ix_suggestions_document_id", table_name="suggestions")
