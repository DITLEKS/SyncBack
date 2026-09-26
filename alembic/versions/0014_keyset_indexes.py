"""
Добавляет составные индексы для keyset-пагинации.

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-26

Индексы:
  ix_documents_project_created_at_id
    — ускоряет list_for_project с KeysetPage
    — покрывает WHERE project_id = ? ORDER BY created_at DESC, id DESC

  ix_suggestions_job_created_at_id
    — ускоряет list_by_analysis_job с KeysetPage
    — покрывает WHERE analysis_job_id = ? ORDER BY created_at ASC, id ASC

Примечание: CREATE INDEX CONCURRENTLY нельзя использовать внутри транзакции
(Alembic по умолчанию оборачивает миграции в транзакцию), поэтому используется
обычный CREATE INDEX. На продакшн-базе с большими таблицами лучше применить
вручную через CONCURRENTLY без Alembic, чтобы не блокировать таблицу.
"""
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_documents_project_created_at_id",
        "documents",
        ["project_id", op.f("created_at DESC"), op.f("id DESC")],
        unique=False,
    )
    op.create_index(
        "ix_suggestions_job_created_at_id",
        "suggestions",
        ["analysis_job_id", "created_at", "id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_suggestions_job_created_at_id", table_name="suggestions")
    op.drop_index("ix_documents_project_created_at_id", table_name="documents")
