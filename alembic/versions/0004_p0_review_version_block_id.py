"""P0-2/3: review_version on documents, block_id/start_offset/end_offset on suggestions.

Revision ID: 0004_p0_review_version_block_id
Revises: 0004_suggestion_id_nullable
Create Date: 2026-09-20
"""
from alembic import op
import sqlalchemy as sa

revision = "0004_p0_review_version_block_id"
down_revision = "0004_suggestion_id_nullable"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Версия review для оптимистической блокировки
    op.add_column(
        "documents",
        sa.Column("review_version", sa.Integer(), nullable=False, server_default="0"),
    )
    # Якоря правок
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
