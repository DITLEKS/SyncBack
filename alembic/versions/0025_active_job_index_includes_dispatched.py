"""Частичный индекс «одна активная задача на документ» учитывает dispatched.

Revision ID: 0025
Revises: 0024
Create Date: 2026-10-03

Статус dispatched (задача в очереди, воркер ещё не начал) теперь используется,
поэтому индекс должен запрещать вторую активную задачу и в нём.
"""

import sqlalchemy as sa
from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None

INDEX = "uq_analysis_jobs_one_active_per_document"


def upgrade() -> None:
    op.drop_index(INDEX, table_name="analysis_jobs")
    op.create_index(
        INDEX,
        "analysis_jobs",
        ["document_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'dispatched', 'processing')"),
    )


def downgrade() -> None:
    op.drop_index(INDEX, table_name="analysis_jobs")
    op.create_index(
        INDEX,
        "analysis_jobs",
        ["document_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'processing')"),
    )
