"""P0: add review_version to documents, block_id to suggestions

Revision ID: 0004b
Revises: 0004a
Create Date: 2026-09-26
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
    op.add_column(
        "suggestions",
        sa.Column("block_id", sa.UUID(), nullable=True),
    )
    op.create_foreign_key(
        "fk_suggestions_block_id",
        "suggestions",
        "document_blocks",
        ["block_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_suggestions_block_id", "suggestions", type_="foreignkey")
    op.drop_column("suggestions", "block_id")
    op.drop_column("documents", "review_version")
