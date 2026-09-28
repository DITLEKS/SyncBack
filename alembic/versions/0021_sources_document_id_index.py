"""Add index on sources.document_id for fast document-scoped source lookups.

Revision ID: 0021
Revises: 0020b
"""
from alembic import op

revision = "0021"
down_revision = "0020b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_sources_document_id",
        "sources",
        ["document_id"],
        postgresql_where="document_id IS NOT NULL",
    )


def downgrade() -> None:
    op.drop_index("ix_sources_document_id", table_name="sources")
