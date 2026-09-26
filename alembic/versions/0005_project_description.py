"""add project description

Revision ID: 0005_project_description
Revises: 0004_p0_review_version_block_id
Create Date: 2026-08-30
"""

from alembic import op
import sqlalchemy as sa

revision = "0005_project_description"
down_revision = "0004_p0_review_version_block_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("description", sa.String(length=2000), nullable=True))


def downgrade() -> None:
    op.drop_column("projects", "description")
