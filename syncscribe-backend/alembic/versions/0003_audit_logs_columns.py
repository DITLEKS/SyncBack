"""add audit_logs document_id column and constraint

Revision ID: 0003_audit_logs_columns
Revises: 0002_add_download_enum_value
Create Date: 2026-08-12
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0003_audit_logs_columns"
down_revision = "0002_add_download_enum_value"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "audit_logs",
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.create_index("ix_audit_logs_document_id", "audit_logs", ["document_id"])
    op.create_check_constraint(
        "ck_audit_logs_target",
        "audit_logs",
        "(action IN ('accept', 'reject') AND suggestion_id IS NOT NULL AND document_id IS NULL) OR "
        "(action = 'download' AND document_id IS NOT NULL AND suggestion_id IS NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_audit_logs_target", "audit_logs", type_="check")
    op.drop_index("ix_audit_logs_document_id", table_name="audit_logs")
    op.drop_column("audit_logs", "document_id")
