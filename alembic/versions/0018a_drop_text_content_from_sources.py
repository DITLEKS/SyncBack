"""drop text_content from sources, add storage_key NOT NULL when type=file

Revision ID: 0018a
Revises: 0017b
Create Date: 2026-09-27
"""

from alembic import op
import sqlalchemy as sa

revision = "0018a"
down_revision = "0017b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Drop text_content — data must be migrated to MinIO before this migration.
    op.drop_column("sources", "text_content")

    # 2. Simplify CHECK: only file (storage_key) and url (url).
    op.drop_constraint("ck_sources_type_payload", "sources", type_="check")
    op.create_check_constraint(
        "ck_sources_type_payload",
        "sources",
        "(source_type = 'file' AND storage_key IS NOT NULL AND url IS NULL) OR "
        "(source_type = 'url'  AND url IS NOT NULL AND storage_key IS NULL)",
    )

    # 3. Remove 'note' from sourcetype enum (PostgreSQL requires recreate).
    op.execute("ALTER TYPE sourcetype RENAME TO sourcetype_old")
    op.execute("CREATE TYPE sourcetype AS ENUM ('file', 'url')")
    op.execute(
        "ALTER TABLE sources "
        "ALTER COLUMN source_type TYPE sourcetype "
        "USING source_type::text::sourcetype"
    )
    op.execute("DROP TYPE sourcetype_old")


def downgrade() -> None:
    op.execute("ALTER TYPE sourcetype RENAME TO sourcetype_old")
    op.execute("CREATE TYPE sourcetype AS ENUM ('file', 'note', 'url')")
    op.execute(
        "ALTER TABLE sources "
        "ALTER COLUMN source_type TYPE sourcetype "
        "USING source_type::text::sourcetype"
    )
    op.execute("DROP TYPE sourcetype_old")
    op.drop_constraint("ck_sources_type_payload", "sources", type_="check")
    op.create_check_constraint(
        "ck_sources_type_payload",
        "sources",
        "(source_type = 'file' AND storage_key IS NOT NULL) OR "
        "(source_type = 'note' AND text_content IS NOT NULL) OR "
        "(source_type = 'url'  AND url IS NOT NULL)",
    )
    op.add_column(
        "sources",
        sa.Column("text_content", sa.Text, nullable=True),
    )
