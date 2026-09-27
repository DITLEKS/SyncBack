"""Add documents.current_analysis_job_id and extend document_status enum.

Revision ID: 0023
Revises: 0022
Create Date: 2026-09-27

Изменения (соответствуют коммиту f6d5afb):
  1. documents.current_analysis_job_id — UUID nullable FK → analysis_jobs(id)
     ON DELETE SET NULL. Используется для быстрого доступа к текущему заданию
     без JOIN: Document.current_analysis_job (relationship в models/document.py).
  2. ix_documents_current_analysis_job_id — индекс для обратного поиска
     «какой документ сейчас анализируется данным job».
  3. document_status enum: добавлены значения 'error' и 'cancelled'
     для синхронизации с DocumentStatusVO (value_objects.py).

downgrade: DROP COLUMN, DROP INDEX, а также safe-удаление новых enum-значений
     через пересоздание типа (PostgreSQL не поддерживает DROP VALUE для enum).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Расширяем enum до применения ALTER TABLE
    op.execute("ALTER TYPE document_status ADD VALUE IF NOT EXISTS 'error'")
    op.execute("ALTER TYPE document_status ADD VALUE IF NOT EXISTS 'cancelled'")

    # 2. Новая колонка на documents
    op.add_column(
        "documents",
        sa.Column(
            "current_analysis_job_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey(
                "analysis_jobs.id",
                name="fk_documents_current_analysis_job_id",
                ondelete="SET NULL",
            ),
            nullable=True,
        ),
    )

    # 3. Индекс для обратного поиска job → document
    op.create_index(
        "ix_documents_current_analysis_job_id",
        "documents",
        ["current_analysis_job_id"],
        postgresql_where=sa.text("current_analysis_job_id IS NOT NULL"),
    )


def downgrade() -> None:
    # 1. Убираем индекс и колонку
    op.drop_index(
        "ix_documents_current_analysis_job_id",
        table_name="documents",
    )
    op.drop_constraint(
        "fk_documents_current_analysis_job_id",
        "documents",
        type_="foreignkey",
    )
    op.drop_column("documents", "current_analysis_job_id")

    # 2. PostgreSQL не поддерживает DROP VALUE для enum.
    # Пересоздаём тип без 'error' / 'cancelled'.
    # Предварительно обновляем строки, чтобы не нарушить NOT NULL constraints
    # (на практике значения не должны присутствовать при откате).
    op.execute(
        """
        UPDATE documents
        SET status = 'draft'
        WHERE status IN ('error', 'cancelled')
        """
    )
    op.execute(
        """
        ALTER TYPE document_status RENAME TO document_status_old;
        CREATE TYPE document_status AS ENUM (
            'draft', 'in_progress', 'awaiting_approval', 'approved'
        );
        ALTER TABLE documents
            ALTER COLUMN status TYPE document_status
            USING status::text::document_status;
        DROP TYPE document_status_old;
        """
    )
