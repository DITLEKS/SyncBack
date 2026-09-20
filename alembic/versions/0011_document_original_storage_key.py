"""
P0-7 (#7): режим «Оригинал» — сохранение снапшота исходного файла до правок.

Добавляет колонку original_storage_key в таблицу documents.
Колонка nullable: заполняется пайплайном анализа при первом запуске.
До первого анализа колонка NULL — DocumentService.get_original_content()
фоллбэчится на storage_key (текущий файл).

Revision ID: 0011
Revises:     0010
Create Date: 2026-09-20
"""

from alembic import op
import sqlalchemy as sa

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column(
            "original_storage_key",
            sa.String(1024),
            nullable=True,
            comment=(
                "MinIO-ключ снапшота исходного файла до применения правок. "
                "Выставляется пайплайном анализа. NULL до первого анализа."
            ),
        ),
    )


def downgrade() -> None:
    op.drop_column("documents", "original_storage_key")
