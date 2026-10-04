"""Аудит-лог принимает все действия, которые пишет приложение.

Revision ID: 0026
Revises: 0025
Create Date: 2026-10-04

Ограничение из 0003 требовало document_id IS NULL у решений по правкам и знало
только accept/reject/download, а в enum audit_action не было reset, bulk_reject
и finalize_review. Приложение пишет решения вместе с document_id и использует
reset, поэтому каждая запись решения в PostgreSQL отклонялась.

Новое правило: действия над документом (download, finalize, finalize_review)
ссылаются на документ и не ссылаются на правку; остальные действия ссылаются
на правку. Старые строки ему удовлетворяют.
"""

from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None

NAME = "ck_audit_logs_target"

NEW_CHECK = (
    "(action IN ('download', 'finalize', 'finalize_review') "
    "AND document_id IS NOT NULL AND suggestion_id IS NULL) OR "
    "(action NOT IN ('download', 'finalize', 'finalize_review') "
    "AND suggestion_id IS NOT NULL)"
)

OLD_CHECK = (
    "(action IN ('accept', 'reject') AND suggestion_id IS NOT NULL AND document_id IS NULL) OR "
    "(action = 'download' AND document_id IS NOT NULL AND suggestion_id IS NULL)"
)


NEW_ENUM_VALUES = ("bulk_reject", "finalize_review", "reset")


def upgrade() -> None:
    # Новое значение enum нельзя использовать в той же транзакции, где оно добавлено,
    # а ограничение ниже на него ссылается.
    with op.get_context().autocommit_block():
        for value in NEW_ENUM_VALUES:
            op.execute(f"ALTER TYPE audit_action ADD VALUE IF NOT EXISTS '{value}'")
    op.drop_constraint(NAME, "audit_logs", type_="check")
    op.create_check_constraint(NAME, "audit_logs", NEW_CHECK)


def downgrade() -> None:
    op.drop_constraint(NAME, "audit_logs", type_="check")
    # Записи, сделанные после upgrade, старому правилу не удовлетворяют, поэтому
    # ограничение возвращается без проверки существующих строк. Значения enum
    # PostgreSQL удалять не умеет, они остаются.
    op.execute(f"ALTER TABLE audit_logs ADD CONSTRAINT {NAME} CHECK ({OLD_CHECK}) NOT VALID")
