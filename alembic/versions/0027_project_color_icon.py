"""Цвет и иконка карточки проекта.

Revision ID: 0027
Revises: 0026
Create Date: 2026-10-04

API принимало color и icon при создании проекта, но хранить их было негде,
поэтому ответ всегда содержал null. Старым проектам цвет назначается по кругу
палитры в порядке создания внутри владельца — так же, как новым.
"""

import sqlalchemy as sa
from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None

# Копия палитры на момент миграции: миграция не должна зависеть от кода приложения.
PALETTE = ("3B82F6", "8B5CF6", "10B981", "F59E0B", "EF4444", "EC4899", "14B8A6", "F97316")


def upgrade() -> None:
    op.add_column("projects", sa.Column("color", sa.String(length=6), nullable=True))
    op.add_column("projects", sa.Column("icon", sa.String(length=64), nullable=True))
    palette = "ARRAY[" + ", ".join(f"'{c}'" for c in PALETTE) + "]"
    op.execute(
        f"""
        UPDATE projects AS p
        SET color = ({palette})[((ranked.n - 1) % {len(PALETTE)}) + 1]
        FROM (
            SELECT id, row_number() OVER (PARTITION BY owner_id ORDER BY created_at, id) AS n
            FROM projects
        ) AS ranked
        WHERE p.id = ranked.id
        """
    )


def downgrade() -> None:
    op.drop_column("projects", "icon")
    op.drop_column("projects", "color")
