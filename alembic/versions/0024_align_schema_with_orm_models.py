"""Привести схему к ORM-моделям.

Revision ID: 0024
Revises: 0023
Create Date: 2026-10-03

Источник истины для схемы — ORM-модели. Ревизия закрывает расхождения,
накопившиеся из-за того, что миграции 0013–0023 писались под разные версии
моделей:

- suggestions: old_text → original_text, new_text → suggested_text,
  explanation → rationale (данные сохраняются); удалены неиспользуемые
  source_reference и order_index; удалены индексы, дублирующие
  ix_suggestions_job_status и ix_suggestions_document_status;
- created_at NOT NULL в analysis_jobs, audit_logs, documents, projects,
  suggestions, users (у всех есть server_default now());
- audit_logs.user_id nullable — FK объявлен ON DELETE SET NULL;
- document_sources: лишнее уникальное ограничение поверх первичного ключа;
- users.email: один уникальный индекс вместо ограничения плюс индекса;
- documents.original_storage_key: комментарий колонки перенесён в код.

После применения `alembic check` на этой ревизии не должен находить изменений.
"""

import sqlalchemy as sa
from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None

_TABLES_WITH_CREATED_AT = (
    "analysis_jobs",
    "audit_logs",
    "documents",
    "projects",
    "suggestions",
    "users",
)

_SUGGESTION_RENAMES = (
    ("old_text", "original_text"),
    ("new_text", "suggested_text"),
    ("explanation", "rationale"),
)


def upgrade() -> None:
    for table in _TABLES_WITH_CREATED_AT:
        op.execute(f"UPDATE {table} SET created_at = now() WHERE created_at IS NULL")
        op.alter_column(
            table,
            "created_at",
            existing_type=sa.DateTime(timezone=True),
            nullable=False,
            existing_server_default=sa.text("now()"),
        )

    op.alter_column("audit_logs", "user_id", existing_type=sa.UUID(), nullable=True)

    op.drop_constraint("uq_document_sources_document_source", "document_sources", type_="unique")

    op.alter_column(
        "documents",
        "original_storage_key",
        existing_type=sa.String(1024),
        existing_nullable=True,
        comment=None,
    )

    for old, new in _SUGGESTION_RENAMES:
        op.alter_column("suggestions", old, new_column_name=new)
    op.drop_column("suggestions", "source_reference")
    op.drop_column("suggestions", "order_index")
    op.drop_index("ix_suggestions_analysis_job_status", table_name="suggestions")
    op.drop_index("ix_suggestions_document_id", table_name="suggestions")

    op.drop_constraint("users_email_key", "users", type_="unique")
    op.drop_index("ix_users_email", table_name="users")
    op.create_index("ix_users_email", "users", ["email"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_users_email", table_name="users")
    op.create_index("ix_users_email", "users", ["email"])
    op.create_unique_constraint("users_email_key", "users", ["email"])

    op.create_index("ix_suggestions_document_id", "suggestions", ["document_id"])
    op.create_index(
        "ix_suggestions_analysis_job_status", "suggestions", ["analysis_job_id", "status"]
    )
    op.add_column(
        "suggestions",
        sa.Column("order_index", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("suggestions", sa.Column("source_reference", sa.String(500), nullable=True))
    for old, new in _SUGGESTION_RENAMES:
        op.alter_column("suggestions", new, new_column_name=old)

    op.alter_column(
        "documents",
        "original_storage_key",
        existing_type=sa.String(1024),
        existing_nullable=True,
        comment=(
            "MinIO-ключ снапшота исходного файла до применения правок. "
            "Выставляется пайплайном анализа. NULL до первого анализа."
        ),
    )

    op.create_unique_constraint(
        "uq_document_sources_document_source", "document_sources", ["document_id", "source_id"]
    )

    op.alter_column("audit_logs", "user_id", existing_type=sa.UUID(), nullable=False)

    for table in reversed(_TABLES_WITH_CREATED_AT):
        op.alter_column(
            table,
            "created_at",
            existing_type=sa.DateTime(timezone=True),
            nullable=True,
            existing_server_default=sa.text("now()"),
        )
