"""make audit_logs.suggestion_id nullable

Revision ID: 0004_audit_logs_suggestion_id_nullable
Revises: 0003_audit_logs_columns
Create Date: 2026-08-30

ИСПРАВЛЕНО: миграция 0003 добавила audit_logs.document_id и CHECK-constraint
ck_audit_logs_target, который требует suggestion_id IS NULL для action='download',
но забыла снять NOT NULL с suggestion_id, унаследованный из миграции 0001 (где
аудит существовал только для accept/reject). В результате любая запись с
action='download' падала с NotNullViolationError на уровне БД, хотя ORM-модель
AuditLog уже объявляет suggestion_id как Mapped[uuid.UUID | None] (nullable=True) —
модель и реальная схема были рассинхронизированы.
"""

from alembic import op

revision = "0004_audit_logs_suggestion_id_nullable"
down_revision = "0003_audit_logs_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("audit_logs", "suggestion_id", nullable=True)


def downgrade() -> None:
    op.alter_column("audit_logs", "suggestion_id", nullable=False)
