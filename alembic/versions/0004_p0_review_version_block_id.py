"""P0-2/3: block_id/start_offset/end_offset on suggestions.

NOTE: review_version on documents was originally added here but has been
moved to 0009 to avoid a DuplicateColumn error on fresh upgrades.
This migration only adds the suggestion anchor columns.

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
    # Якоря правок в блочной модели документа
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
