"""
add dashboard_snapshots table

Revision ID: 0010a
Revises: 0009
Create Date: 2026-09-27
"""

from alembic import op
import sqlalchemy as sa

revision = "0010a"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dashboard_snapshots",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "owner_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("snapshot_date", sa.Date, nullable=False),
        sa.Column("total_count", sa.Integer, nullable=False),
        sa.Column("awaiting_count", sa.Integer, nullable=False),
        sa.Column("relevance_percent", sa.Float, nullable=False),
        sa.UniqueConstraint("owner_id", "snapshot_date", name="uq_dashboard_snapshot_owner_date"),
    )
    op.create_index(
        "ix_dashboard_snapshots_owner_id",
        "dashboard_snapshots",
        ["owner_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_dashboard_snapshots_owner_id", table_name="dashboard_snapshots")
    op.drop_table("dashboard_snapshots")
