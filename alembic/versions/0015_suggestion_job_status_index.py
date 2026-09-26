"""
Добавляет составной индекс для фильтрации по задаче + статусу.

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-26

Индекс:
  ix_suggestions_job_status
    (analysis_job_id, status)

Покрываемые запросы:

  1. list_with_total (H-3)
     SELECT ... COUNT(*) OVER() ... WHERE analysis_job_id = ?
     ORDER BY created_at, id LIMIT ? OFFSET ?
     → индекс 0014 (job_id, created_at, id) покрывает сортировку,
       но не помогает count_by_analysis_job_and_status.

  2. bulk_accept_all (H-4)
     UPDATE suggestions SET status='accepted'
     WHERE analysis_job_id = ? AND status = 'pending'
     → без этого индекса — seq scan по всей таблице.

  3. count_by_analysis_job_and_status
     SELECT COUNT(*) WHERE analysis_job_id = ? AND status = ?
     → используется при finalize_review и atomic_review_save.

  4. list_by_analysis_job_and_status
     SELECT ... WHERE analysis_job_id = ? AND status = ?
     → используется при экспорте документа.

Примечание: на продакшн-базе с большой таблицей применяйте
  CREATE INDEX CONCURRENTLY ix_suggestions_job_status
    ON suggestions(analysis_job_id, status);
вручную вне Alembic, чтобы не блокировать записи.
"""
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_suggestions_job_status",
        "suggestions",
        ["analysis_job_id", "status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_suggestions_job_status", table_name="suggestions")
