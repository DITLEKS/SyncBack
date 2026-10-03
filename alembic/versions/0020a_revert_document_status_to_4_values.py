"""revert document_status to 4 values, migrate existing error/cancelled rows to draft

Revision ID: 0020a
Revises: 0019
Create Date: 2026-09-28

4STATUS: Public document status is limited to 4 values.
This migration:
  1. Moves all documents with 'error'/'cancelled' status to 'draft'.
  2. Recreates the PostgreSQL enum document_status without 'error' and 'cancelled'.

ODOWN: restores 'error' and 'cancelled' in the enum but does NOT convert rows back
(rows remain 'draft' — previous status was not persisted separately).
"""

from alembic import op
import sqlalchemy as sa

revision = "0020a"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Convert existing rows with technical statuses → draft
    op.execute("""
        UPDATE documents
        SET status = 'draft'
        WHERE status::text IN ('error', 'cancelled');
    """)

    # 2. Recreate enum without 'error' and 'cancelled'
    op.execute("ALTER TYPE document_status RENAME TO document_status_old;")
    op.execute("""
        CREATE TYPE document_status AS ENUM
            ('draft', 'in_progress', 'awaiting_approval', 'ready');
    """)
    op.execute("""
        ALTER TABLE documents
            ALTER COLUMN status DROP DEFAULT,
            ALTER COLUMN status TYPE document_status
                USING status::text::document_status,
            ALTER COLUMN status SET DEFAULT 'draft';
    """)
    op.execute("DROP TYPE document_status_old;")


def downgrade() -> None:
    op.execute("ALTER TYPE document_status RENAME TO document_status_old;")
    op.execute("""
        CREATE TYPE document_status AS ENUM
            ('draft', 'in_progress', 'awaiting_approval', 'ready', 'error', 'cancelled');
    """)
    op.execute("""
        ALTER TABLE documents
            ALTER COLUMN status DROP DEFAULT,
            ALTER COLUMN status TYPE document_status
                USING status::text::document_status,
            ALTER COLUMN status SET DEFAULT 'draft';
    """)
    op.execute("DROP TYPE document_status_old;")
