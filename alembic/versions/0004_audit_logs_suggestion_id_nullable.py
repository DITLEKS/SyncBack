"""make audit_logs.suggestion_id nullable

Revision ID: 0004_suggestion_id_nullable
Revises: 0003_audit_logs_columns
Create Date: 2026-08-30

ИСПРАВЛЕНО: миграция 0003 добавила audit_logs.document_id и CHECK-constraint
ck_audit_logs_target, который требует suggestion_id IS NULL для action='download',
но забыла снять NOT NULL с suggestion_id, унаследованный из миграции 0001.

ИСПРАВЛЕНО (v2): первоначальный revision id
"0004_audit_logs_suggestion_id_nullable" (39 символов) превышал допустимый размер
колонки alembic_version.version_num (VARCHAR(32) по умолчанию у Alembic), и
`alembic upgrade head` падал с asyncpg.exceptions.StringDataRightTruncationError при
попытке записать новый revision в эту таблицу (благодаря
`transaction_per_migration=True` в alembic/env.py вся транзакция, включая ALTER COLUMN,
откатилась целиком, без частичного применения). Revision id укорочен до
27 символов.
"""

from alembic import op

revision = "0004_suggestion_id_nullable"
down_revision = "0003_audit_logs_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("audit_logs", "suggestion_id", nullable=True)


def downgrade() -> None:
    op.alter_column("audit_logs", "suggestion_id", nullable=False)
