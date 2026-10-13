"""Ручной вариант текста предложения, отдельно от текста ИИ.

Revision ID: 0028
Revises: 0027
"""

import sqlalchemy as sa
from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("suggestions", sa.Column("edited_text", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("suggestions", "edited_text")
