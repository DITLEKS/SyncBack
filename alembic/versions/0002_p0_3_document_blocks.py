"""
P0-3: document_blocks table

Revision ID: 0002_p0_3
Revises: 0001  # замени на реальный revision ID из своего alembic/versions/
Create Date: 2026-09-20

TODO: перед применением:
  1. Заменить down_revision на head-ревизию в твоей БД
  2. Запустить alembic upgrade head локально и проверить
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002_p0_3"
down_revision = "0001"  # TODO: замени на реальный
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "document_blocks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("document_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("position", sa.Integer, nullable=False),
        sa.Column(
            "block_type",
            sa.Enum("heading", "paragraph", "list_item", "code", "table", "other",
                    name="block_type_enum"),
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
    # Опционально: добавить block_id FK в suggestions
    # op.add_column("suggestions", sa.Column(
    #     "block_id", postgresql.UUID(as_uuid=True),
    #     sa.ForeignKey("document_blocks.id", ondelete="SET NULL"),
    #     nullable=True,
    # ))


def downgrade() -> None:
    # op.drop_column("suggestions", "block_id")
    op.drop_index("ix_document_blocks_position", table_name="document_blocks")
    op.drop_index("ix_document_blocks_document_id", table_name="document_blocks")
    op.drop_table("document_blocks")
    op.execute("DROP TYPE IF EXISTS block_type_enum")
