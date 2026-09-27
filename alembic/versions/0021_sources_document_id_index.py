"""I-1 / FIX-4: индексы на document_sources для батч-запроса list_by_document_ids.

Revision ID: 0021
Revises: 0020
Create Date: 2026-09-27

FIX-4: предыдущая версия создавала индекс на sources(project_id, document_id),
но колонки document_id в таблице sources нет — связь documents<->sources
идёт через M2M-таблицу document_sources.

Заменяем на два индекса по document_sources:
  1. ix_document_sources_document_id
       — ускоряет WHERE ds.document_id IN (:ids) при батч-выборке.
  2. ix_document_sources_source_id
       — ускоряет обратный JOIN (source_id -> sources.id).

Частичный WHERE scope='document' убран: document_sources содержит
только document-scope связи по определению (project-scope источники
не вносятся в эту таблицу).
"""
from __future__ import annotations

from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Индекс 1: основной — батч-фильтр по document_id
    op.create_index(
        "ix_document_sources_document_id",
        "document_sources",
        ["document_id"],
    )
    # Индекс 2: покрывающий JOIN document_sources -> sources
    op.create_index(
        "ix_document_sources_source_id",
        "document_sources",
        ["source_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_document_sources_source_id", table_name="document_sources")
    op.drop_index("ix_document_sources_document_id", table_name="document_sources")
