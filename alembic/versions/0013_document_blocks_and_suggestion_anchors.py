"""Add document blocks and suggestion anchors.

Revision ID: 0013
Revises: 0012
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    block_type = postgresql.ENUM(
        "heading", "paragraph", "list_item", "code", "table", "other",
        name="block_type_enum",
    )
    block_type.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "document_blocks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column(
            "block_type",
            postgresql.ENUM(name="block_type_enum", create_type=False),
            nullable=False,
        ),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("heading_level", sa.Integer(), nullable=True),
        sa.Column("raw_markdown", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_document_blocks_document_position",
        "document_blocks",
        ["document_id", "position"],
        unique=True,
    )
    op.add_column("suggestions", sa.Column("block_id", sa.String(255), nullable=True))
    op.add_column("suggestions", sa.Column("start_offset", sa.Integer(), nullable=True))
    op.add_column("suggestions", sa.Column("end_offset", sa.Integer(), nullable=True))
    op.create_index(
        "ix_suggestions_analysis_job_status",
        "suggestions",
        ["analysis_job_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_suggestions_analysis_job_status", table_name="suggestions")
    op.drop_column("suggestions", "end_offset")
    op.drop_column("suggestions", "start_offset")
    op.drop_column("suggestions", "block_id")
    op.drop_index("ix_document_blocks_document_position", table_name="document_blocks")
    op.drop_table("document_blocks")
    postgresql.ENUM(name="block_type_enum").drop(op.get_bind(), checkfirst=True)
