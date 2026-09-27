"""revert document_status to 4 values, migrate existing error/cancelled rows to draft

Revision ID: 0020
Revises: 0019
Create Date: 2026-09-28

4STATUS: Публичный статус документа ограничен 4 значениями.
Эта миграция:
  1. Переводит все документы со статусами 'error' / 'cancelled' в 'draft'.
  2. Пересоздаёт PostgreSQL enum document_status без значений 'error' и 'cancelled'.

ODOWN: восстанавливает 'error' и 'cancelled' в enum и обратно не конвертирует
(строки остаются 'draft' — данные о прежнем статусе не хранились).
"""
from alembic import op
import sqlalchemy as sa

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Конвертируем существующие строки с техническими статусами → draft
    op.execute("""
        UPDATE documents
        SET status = 'draft'
        WHERE status::text IN ('error', 'cancelled');
    """)

    # 2. Пересоздаём enum: убираем 'error' и 'cancelled'
    #    PostgreSQL не поддерживает DROP VALUE напрямую — используем rename+recreate.
    op.execute("""
        ALTER TYPE document_status RENAME TO document_status_old;
    """)
    op.execute("""
        CREATE TYPE document_status AS ENUM
            ('draft', 'in_progress', 'awaiting_approval', 'ready');
    """)
    op.execute("""
        ALTER TABLE documents
            ALTER COLUMN status DROP DEFAULT,
            ALTER COLUMN status TYPE document_status
                USING status::text::document_status,
            ALTER COLUMN status SET DEFAULT 'draft';
    """)
    op.execute("""
        DROP TYPE document_status_old;
    """)


def downgrade() -> None:
    # Восстанавливаем enum с 6 значениями (данные уже потеряны — строки стали 'draft')
    op.execute("""
        ALTER TYPE document_status RENAME TO document_status_old;
    """)
    op.execute("""
        CREATE TYPE document_status AS ENUM
            ('draft', 'in_progress', 'awaiting_approval', 'ready', 'error', 'cancelled');
    """)
    op.execute("""
        ALTER TABLE documents
            ALTER COLUMN status DROP DEFAULT,
            ALTER COLUMN status TYPE document_status
                USING status::text::document_status,
            ALTER COLUMN status SET DEFAULT 'draft';
    """)
    op.execute("""
        DROP TYPE document_status_old;
    """)
