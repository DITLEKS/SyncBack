"""P0-7: add idempotency_key to analysis_jobs

Revision ID: a1b2c3d4e5f6
Revises: 
Create Date: 2026-09-20

Добавляет:
- колонку idempotency_key (VARCHAR 128, nullable) в таблицу analysis_jobs
- частичный уникальный индекс (document_id, idempotency_key) WHERE idempotency_key IS NOT NULL
  вместо полного UniqueConstraint — NULL-строки не должны конфликтовать между собой.
- колонку partial_success (BOOLEAN, NOT NULL, default false) в таблицу analysis_jobs

Откат (downgrade) удаляет обе колонки и индекс.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "a1b2c3d4e5f6"
down_revision = None  # TODO: заменить на реальный head-revision после инициализации alembic
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- idempotency_key ---
    op.add_column(
        "analysis_jobs",
        sa.Column("idempotency_key", sa.String(128), nullable=True),
    )
    # Частичный уникальный индекс: NULL-значения не нарушают уникальность в PostgreSQL,
    # но для надёжности используем явный WHERE idempotency_key IS NOT NULL.
    op.create_index(
        "ix_analysis_jobs_doc_idem_key",
        "analysis_jobs",
        ["document_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )

    # --- partial_success ---
    op.add_column(
        "analysis_jobs",
        sa.Column(
            "partial_success",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_index("ix_analysis_jobs_doc_idem_key", table_name="analysis_jobs")
    op.drop_column("analysis_jobs", "idempotency_key")
    op.drop_column("analysis_jobs", "partial_success")
