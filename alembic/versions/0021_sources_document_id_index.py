"""I-1: partial index on sources.document_id for document-scope badge batch-query.

Revision ID: 0021
Revises: 0020
Create Date: 2026-09-27
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Partial index: только строки с document_id IS NOT NULL (scope='document').
    # Используется батч-запросом list_by_document_ids в source_repository.
    # CONCURRENTLY нельзя внутри транзакции Alembic — используем обычный CREATE.
    op.create_index(
        "ix_sources_document_id",
        "sources",
        ["document_id"],
        postgresql_where=sa.text("document_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_sources_document_id", table_name="sources")
