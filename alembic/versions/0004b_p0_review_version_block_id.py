"""P0: add review_version to documents

Revision ID: 0004b
Revises: 0004a
Create Date: 2026-09-26

suggestions.block_id из прежней версии этой ревизии (UUID → document_blocks.id)
убран: колонка добавляется в 0013 как varchar-ссылка на document_blocks.block_ref,
как в ORM-модели.
"""

import sqlalchemy as sa
from alembic import op

revision = "0004b"
down_revision = "0004a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("review_version", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("documents", "review_version")
