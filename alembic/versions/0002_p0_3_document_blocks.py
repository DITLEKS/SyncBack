"""
P0-3: document_blocks table

Revision ID: 0002_p0_3
Revises: 0002_add_download_enum_value
Create Date: 2026-09-20
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002_p0_3"
down_revision = "0002_add_download_enum_value"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "document_blocks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer, nullable=False),
        sa.Column(
            "block_type",
            sa.Enum(
                "heading", "paragraph", "list_item", "code", "table", "other",
                name="block_type_enum",
            ),
            nullable=False,
        ),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("heading_level", sa.Integer, nullable=True),
        sa.Column("raw_markdown", sa.Text, nullable=True),
    )
    op.create_index("ix_document_blocks_document_id", "document_blocks", ["document_id"])
    op.create_index(
        "ix_document_blocks_position", "document_blocks", ["document_id", "position"]
    )


def downgrade() -> None:
    op.drop_index("ix_document_blocks_position", table_name="document_blocks")
    op.drop_index("ix_document_blocks_document_id", table_name="document_blocks")
    op.drop_table("document_blocks")
    op.execute("DROP TYPE IF EXISTS block_type_enum")
