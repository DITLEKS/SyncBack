"""Связи правок с источниками, на основании которых они созданы.

Revision ID: 0032
Revises: 0031
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "suggestion_sources",
        sa.Column("suggestion_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["suggestion_id"], ["suggestions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("suggestion_id", "source_id"),
    )
    op.create_index(
        "ix_suggestion_sources_source_id", "suggestion_sources", ["source_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_suggestion_sources_source_id", table_name="suggestion_sources")
    op.drop_table("suggestion_sources")
