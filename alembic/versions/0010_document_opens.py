"""
Миграция: таблица document_opens для трекинга last_opened_at.

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-20
"""
from alembic import op
import sqlalchemy as sa

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "document_opens",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "document_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "last_opened_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    # уникальный индекс (user_id, document_id) — один трек на пару
    op.create_unique_constraint(
        "uq_document_opens_user_document",
        "document_opens",
        ["user_id", "document_id"],
    )
    # индекс для быстрой выборки последних открытий по пользователю
    op.create_index(
        "ix_document_opens_user_opened",
        "document_opens",
        ["user_id", "last_opened_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_document_opens_user_opened", table_name="document_opens")
    op.drop_constraint("uq_document_opens_user_document", "document_opens")
    op.drop_table("document_opens")
