"""suggestions.source_id: источник, на основании которого создана правка.

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
    op.add_column(
        "suggestions",
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_suggestions_source_id_sources",
        "suggestions",
        "sources",
        ["source_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_suggestions_source_id", "suggestions", ["source_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_suggestions_source_id", table_name="suggestions")
    op.drop_constraint("fk_suggestions_source_id_sources", "suggestions", type_="foreignkey")
    op.drop_column("suggestions", "source_id")
