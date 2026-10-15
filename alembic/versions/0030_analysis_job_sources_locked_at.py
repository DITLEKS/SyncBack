"""analysis_job_sources.locked_at: захват источника перед вызовом LLM (P0-2b).

Revision ID: 0030
Revises: 0029
"""

import sqlalchemy as sa
from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "analysis_job_sources",
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("analysis_job_sources", "locked_at")
