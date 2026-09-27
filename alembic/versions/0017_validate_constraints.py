"""Validate NOT VALID constraints added in 0016.

Revision ID: 0017
Revises: 0016
Create Date: 2026-09-27

All constraints were added with NOT VALID in 0016 to avoid full-table scans
during the migration itself. This follow-up migration validates them.

VALIDATE CONSTRAINT takes ShareUpdateExclusiveLock (allows reads and writes)
instead of AccessExclusiveLock, so it is safe to run on a live production DB.

Run this migration during a low-traffic window or with a statement_timeout
set appropriately for your dataset size.
"""
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE documents "
        "VALIDATE CONSTRAINT ck_documents_size_bytes_non_negative"
    )
    op.execute(
        "ALTER TABLE audit_logs "
        "VALIDATE CONSTRAINT ck_audit_logs_ref_not_null"
    )
    op.execute(
        "ALTER TABLE suggestions "
        "VALIDATE CONSTRAINT ck_suggestions_confidence_range"
    )
    op.execute(
        "ALTER TABLE document_blocks "
        "VALIDATE CONSTRAINT ck_document_blocks_heading_level"
    )
    op.execute(
        "ALTER TABLE sources "
        "VALIDATE CONSTRAINT ck_sources_type_field_consistency"
    )


def downgrade() -> None:
    # Re-mark constraints as NOT VALID (no data is changed)
    # PostgreSQL does not support directly re-marking as NOT VALID after validation;
    # the safest downgrade is a no-op. Constraints remain valid but marked validated.
    pass
