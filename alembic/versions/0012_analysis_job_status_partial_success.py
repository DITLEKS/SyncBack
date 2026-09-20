"""P0-7: добавить значение partial_success в PostgreSQL-тип analysis_job_status.

Python-enum AnalysisJobStatus.PARTIAL_SUCCESS уже объявлен в enums.py,
но ALTER TYPE ... ADD VALUE необходим, чтобы PostgreSQL принял это значение
при записи через SQLAlchemy (иначе INSERT/UPDATE с partial_success упадёт
с ProgrammingError: invalid input value for enum).

Revision ID: 0012
Revises:     0011
Create Date: 2026-09-20
"""

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ADD VALUE не требует COMMIT — в PostgreSQL 12+ он выполняется в транзакции.
    # IF NOT EXISTS защищает от повторного применения (idempotent).
    op.execute(
        "ALTER TYPE analysis_job_status ADD VALUE IF NOT EXISTS 'partial_success'"
    )


def downgrade() -> None:
    # PostgreSQL не поддерживает удаление значений из enum без пересоздания типа.
    # При откате оставляем значение в типе — это безопасно: строк с partial_success
    # после downgrade не должно быть (они удаляются логикой выше по цепочке).
    pass
