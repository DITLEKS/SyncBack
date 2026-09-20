"""P0-2/3: review_version на documents, block_id/start_offset/end_offset на suggestions.

Revision ID: 0004_p0_review_version_block_id
Revises: 0003_p0_idempotency_key
Create Date: 2026-09-20
"""
from alembic import op
import sqlalchemy as sa

revision = "0004_p0_review_version_block_id"
down_revision = "0003_p0_idempotency_key"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # P0-2: версия review для оптимистической блокировки
    op.add_column(
        "documents",
        sa.Column("review_version", sa.Integer(), nullable=False, server_default="0"),
    )

    # P0-3: якоря правок
    op.add_column(
        "suggestions",
        sa.Column("block_id", sa.String(255), nullable=True),
    )
    op.add_column(
        "suggestions",
        sa.Column("start_offset", sa.Integer(), nullable=True),
    )
    op.add_column(
        "suggestions",
        sa.Column("end_offset", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("suggestions", "end_offset")
    op.drop_column("suggestions", "start_offset")
    op.drop_column("suggestions", "block_id")
    op.drop_column("documents", "review_version")
