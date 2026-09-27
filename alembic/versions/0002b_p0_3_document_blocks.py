"""P0-3: add document_blocks table

Revision ID: 0002b
Revises: 0002a
Create Date: 2026-09-26
"""
import sqlalchemy as sa
from alembic import op

revision = "0002b"
down_revision = "0002a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "document_blocks",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("block_type", sa.String(length=64), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["document_id"], ["documents.id"], ondelete="CASCADE"
        ),
    )
    op.create_index(
        "ix_document_blocks_document_id",
        "document_blocks",
        ["document_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_document_blocks_document_id", table_name="document_blocks")
    op.drop_table("document_blocks")
