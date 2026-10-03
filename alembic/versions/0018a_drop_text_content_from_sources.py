"""drop text_content from sources, add storage_key NOT NULL when type=file

Revision ID: 0018a
Revises: 0017b
Create Date: 2026-09-27

Имена объектов соответствуют реальной схеме: колонка sources.type, тип
source_type (после 0016 содержит file, note, link, text, url), ограничение
ck_sources_type_field_consistency из 0016.
"""

import sqlalchemy as sa
from alembic import op

revision = "0018a"
down_revision = "0017b"
branch_labels = None
depends_on = None

_CHECK_NAME = "ck_sources_type_field_consistency"


def _recreate_source_type(values: str) -> None:
    """Пересоздать enum source_type: PostgreSQL не умеет удалять значения иначе."""
    op.execute("ALTER TYPE source_type RENAME TO source_type_old")
    op.execute(f"CREATE TYPE source_type AS ENUM ({values})")
    op.execute(
        "ALTER TABLE sources ALTER COLUMN type TYPE source_type USING type::text::source_type"
    )
    op.execute("DROP TYPE source_type_old")


def upgrade() -> None:
    # CHECK ссылается на колонку type, поэтому снимаем его до смены типа.
    op.drop_constraint(_CHECK_NAME, "sources", type_="check")

    # text_content больше не используется: содержимое источников лежит в MinIO.
    op.drop_column("sources", "text_content")

    # Строки с type = 'text' сюда дойти не должны (их содержимое переносится в MinIO
    # до миграции); если такие есть, приведение типа упадёт — это намеренно.
    _recreate_source_type("'file', 'url'")

    op.create_check_constraint(
        _CHECK_NAME,
        "sources",
        "(type = 'file' AND storage_key IS NOT NULL) OR (type = 'url' AND url IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint(_CHECK_NAME, "sources", type_="check")
    _recreate_source_type("'file', 'note', 'link', 'text', 'url'")
    op.add_column("sources", sa.Column("text_content", sa.Text(), nullable=True))
    op.create_check_constraint(
        _CHECK_NAME,
        "sources",
        "(type = 'url' AND url IS NOT NULL) OR "
        "(type = 'file' AND storage_key IS NOT NULL) OR "
        "(type = 'text' AND text_content IS NOT NULL)",
    )
