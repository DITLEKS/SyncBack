"""I-1: composite partial index on sources(project_id, document_id) for document-scope batch-query.

Revision ID: 0021
Revises: 0020
Create Date: 2026-09-27

P0-fix: ранее индекс создавался только по document_id.
Теперь составной (project_id, document_id) WHERE scope = 'document',
что соответствует запросу list_by_document_ids(project_id, doc_ids).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Составной частичный индекс: покрывает батч-запрос
    #   WHERE project_id = :pid AND document_id IN (...) AND scope = 'document'
    # Partial WHERE убирает project-scope строки из индекса.
    op.create_index(
        "ix_sources_project_document_scope",
        "sources",
        ["project_id", "document_id"],
        postgresql_where=sa.text("scope = 'document'"),
    )


def downgrade() -> None:
    op.drop_index("ix_sources_project_document_scope", table_name="sources")
