"""PERF-OFFSET: составной индекс (analysis_job_id, id) на таблице suggestions.

Покрывает запрос keyset-пагинации целиком:
  SELECT ... FROM suggestions
  WHERE analysis_job_id = :job_id AND id > :after_id
  ORDER BY id LIMIT :limit

PostgreSQL использует индекс как btree-scan по (analysis_job_id, id) —
все нужные строки выбираются из индекса без обращения к heap-таблице
(Index Only Scan при наличии visibility map).

Без индекса при OFFSET-пагинации PG делает Seq Scan или Bitmap Heap Scan
по analysis_job_id + sort, что деградирует при росте таблицы.

Revision ID: 0013
Revises:     0012
Create Date: 2026-09-26
"""

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_suggestions_job_id_id",
        "suggestions",
        ["analysis_job_id", "id"],
        unique=False,
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index("ix_suggestions_job_id_id", table_name="suggestions")
