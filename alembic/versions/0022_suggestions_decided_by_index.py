"""Add index on suggestions.decided_by.

Revision ID: 0022
Revises: 0021
"""
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_suggestions_decided_by",
        "suggestions",
        ["decided_by"],
        postgresql_where="decided_by IS NOT NULL",
    )


def downgrade() -> None:
    op.drop_index("ix_suggestions_decided_by", table_name="suggestions")
