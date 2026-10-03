"""P0-2: добавить колонку review_version в documents.

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-20

Колонка уже добавляется в 0004b, поэтому миграция пропускает шаг, если
колонка существует: цепочка должна применяться с нуля и на базах, где
0004b уже выполнена.
"""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return column in {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    if _has_column("documents", "review_version"):
        return
    op.add_column(
        "documents",
        sa.Column("review_version", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    # Колонку удаляет 0004b; здесь ничего не делаем, чтобы не ломать её downgrade.
    pass
