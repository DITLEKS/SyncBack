"""analysis_job_sources.claim_token: владелец текущего захвата источника.

Revision ID: 0031
Revises: 0030
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "analysis_job_sources",
        sa.Column("claim_token", postgresql.UUID(as_uuid=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("analysis_job_sources", "claim_token")
