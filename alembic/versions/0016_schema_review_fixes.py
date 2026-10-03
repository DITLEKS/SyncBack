"""schema_review_fixes — 14 issues from ORM review (commit 8d70ae0).

Revision ID: 0016
Revises: 0015
Create Date: 2026-09-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _add_enum_value(pg_type: str, value: str) -> None:
    """ALTER TYPE ... ADD VALUE IF NOT EXISTS (idempotent)."""
    op.execute(f"ALTER TYPE {pg_type} ADD VALUE IF NOT EXISTS '{value}'")


def _exec(sql: str) -> None:
    op.execute(sa.text(sql))


# ---------------------------------------------------------------------------
# upgrade
# ---------------------------------------------------------------------------


def upgrade() -> None:
    bind = op.get_bind()

    # ── 1. ENUM extensions ──────────────────────────────────────────────────
    # Новое значение enum нельзя использовать в той же транзакции, где оно
    # добавлено, поэтому ADD VALUE выполняются вне транзакции миграции.
    with op.get_context().autocommit_block():
        # analysis_job_status: add DISPATCHED between PENDING and PROCESSING
        _add_enum_value("analysis_job_status", "dispatched")

        # user_role: add EDITOR, VIEWER
        _add_enum_value("user_role", "editor")
        _add_enum_value("user_role", "viewer")

        # audit_action: add BULK_ACCEPT, FINALIZE, REOPEN
        _add_enum_value("audit_action", "bulk_accept")
        _add_enum_value("audit_action", "finalize")
        _add_enum_value("audit_action", "reopen")

        # source_type: rename note→text, link→url
        # PostgreSQL does NOT support renaming enum values before 14; safest approach:
        # add new values, migrate data, old values become unused (no rows reference them
        # in a fresh DB; in prod you'd also UPDATE sources SET type = 'text' WHERE type='note')
        _add_enum_value("source_type", "text")
        _add_enum_value("source_type", "url")
    # Migrate any legacy data that might exist
    _exec("UPDATE sources SET type = 'text' WHERE type = 'note'")
    _exec("UPDATE sources SET type = 'url'  WHERE type = 'link'")

    # ── 2. document_sources — composite PK (drop surrogate id) ──────────────
    # The table was created in 0001 with a surrogate id column.
    # We: drop the old PK + id column, add composite PK.
    with op.batch_alter_table("document_sources") as batch_op:
        batch_op.drop_column("id")
    # After batch, recreate PK constraint explicitly (SQLite-style batch already
    # handles this; on Postgres we do it directly).
    _exec("ALTER TABLE document_sources ADD PRIMARY KEY (document_id, source_id)")

    # ── 3. document_opens — composite PK (drop surrogate id) ────────────────
    # 0010 created the table with a surrogate id PK + a separate unique constraint.
    # We drop both and promote the unique pair to PK.
    op.drop_constraint("uq_document_opens_user_document", "document_opens", type_="unique")
    with op.batch_alter_table("document_opens") as batch_op:
        batch_op.drop_column("id")
    _exec("ALTER TABLE document_opens ADD PRIMARY KEY (user_id, document_id)")

    # ── 4. documents — updated_at NOT NULL; fix current_analysis_job_id FK ──
    # Колонка создана в 0001 как nullable; здесь только ужесточаем её.
    _exec("UPDATE documents SET updated_at = COALESCE(updated_at, created_at, now())")
    _exec("ALTER TABLE documents ALTER COLUMN updated_at SET NOT NULL")
    _exec("ALTER TABLE documents ALTER COLUMN updated_at SET DEFAULT now()")
    # Re-create FK with SET NULL (previously it was CASCADE or no ondelete).
    # В 0001 ограничение называется fk_documents_current_analysis_job; снимаем оба
    # имени, чтобы на колонке не осталось двух FK.
    _exec("ALTER TABLE documents DROP CONSTRAINT IF EXISTS fk_documents_current_analysis_job")
    _exec("ALTER TABLE documents DROP CONSTRAINT IF EXISTS documents_current_analysis_job_id_fkey")
    _exec(
        "ALTER TABLE documents "
        "ADD CONSTRAINT documents_current_analysis_job_id_fkey "
        "FOREIGN KEY (current_analysis_job_id) "
        "REFERENCES analysis_jobs(id) ON DELETE SET NULL"
    )
    # Колонки name, size_bytes и uploaded_at до этой ревизии ни одна миграция
    # не создавала, хотя ORM-модель, CHECK ниже и индекс в 0019 на них рассчитывают.
    document_columns = {c["name"] for c in sa.inspect(bind).get_columns("documents")}
    if "name" not in document_columns and "title" in document_columns:
        op.alter_column("documents", "title", new_column_name="name", type_=sa.String(512))
    if "size_bytes" not in document_columns:
        op.add_column(
            "documents",
            sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        )
    if "uploaded_at" not in document_columns:
        op.add_column(
            "documents",
            sa.Column(
                "uploaded_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )
        _exec("UPDATE documents SET uploaded_at = created_at WHERE created_at IS NOT NULL")
    _exec(
        "ALTER TABLE documents "
        "ADD CONSTRAINT ck_documents_size_bytes_non_negative "
        "CHECK (size_bytes >= 0) NOT VALID"
    )

    # ── 5. audit_logs ────────────────────────────────────────────────────────
    # 5a. add details JSONB
    op.add_column(
        "audit_logs",
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    # 5b. add document_id column (if not already present from 0003)
    #     0003 added suggestion_id nullable — check if document_id exists
    inspector = sa.inspect(bind)
    existing_cols = {c["name"] for c in inspector.get_columns("audit_logs")}
    if "document_id" not in existing_cols:
        op.add_column(
            "audit_logs",
            sa.Column(
                "document_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("documents.id", ondelete="CASCADE"),
                nullable=True,
                index=True,
            ),
        )
    # 5c. fix user_id FK: CASCADE → SET NULL
    _exec("ALTER TABLE audit_logs DROP CONSTRAINT IF EXISTS audit_logs_user_id_fkey")
    _exec(
        "ALTER TABLE audit_logs "
        "ADD CONSTRAINT audit_logs_user_id_fkey "
        "FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL"
    )
    # 5d. CHECK: at least one of suggestion_id / document_id must be non-null
    _exec(
        "ALTER TABLE audit_logs "
        "ADD CONSTRAINT ck_audit_logs_ref_not_null "
        "CHECK (suggestion_id IS NOT NULL OR document_id IS NOT NULL) NOT VALID"
    )
    # 5e. composite index (document_id, created_at)
    op.create_index(
        "ix_audit_logs_document_created",
        "audit_logs",
        ["document_id", "created_at"],
    )

    # ── 6. suggestions ───────────────────────────────────────────────────────
    # 6a. confidence_score: FLOAT → NUMERIC(4,3)
    _exec(
        "ALTER TABLE suggestions "
        "ALTER COLUMN confidence_score "
        "TYPE NUMERIC(4,3) USING confidence_score::numeric(4,3)"
    )
    # 6b. CHECK constraint on confidence range
    _exec(
        "ALTER TABLE suggestions "
        "ADD CONSTRAINT ck_suggestions_confidence_range "
        "CHECK (confidence_score IS NULL OR "
        "       (confidence_score >= 0 AND confidence_score <= 1)) NOT VALID"
    )
    # 6c. FK suggestions.block_id → document_blocks.block_ref здесь не создаётся:
    #     block_ref уникален только в паре с document_id, и PostgreSQL не примет
    #     ссылку на него в одиночку. Корректная составная ссылка
    #     (document_id, block_id) → (document_id, block_ref) требует правки
    #     ORM-модели и выполняется отдельной миграцией.
    # 6d. add order_index
    op.add_column(
        "suggestions",
        sa.Column("order_index", sa.Integer(), nullable=False, server_default="0"),
    )

    # ── 7. document_blocks — add block_ref ───────────────────────────────────
    op.add_column(
        "document_blocks",
        sa.Column("block_ref", sa.String(255), nullable=True),  # nullable during migration
    )
    # Populate block_ref from existing rows (use position as stable string key)
    _exec(
        "UPDATE document_blocks "
        "SET block_ref = CONCAT(block_type, '-', position::text) "
        "WHERE block_ref IS NULL"
    )
    # Make NOT NULL now that data is populated
    _exec("ALTER TABLE document_blocks ALTER COLUMN block_ref SET NOT NULL")
    # Unique constraint: (document_id, block_ref)
    op.create_unique_constraint(
        "uq_document_blocks_doc_ref",
        "document_blocks",
        ["document_id", "block_ref"],
    )
    # Rename the existing unique index on (document_id, position) to match ORM name
    # ix_document_blocks_document_position → uq_document_blocks_doc_position
    _exec(
        "ALTER INDEX IF EXISTS ix_document_blocks_document_position "
        "RENAME TO uq_document_blocks_doc_position"
    )
    # CHECK: heading_level only for heading blocks
    _exec(
        "ALTER TABLE document_blocks "
        "ADD CONSTRAINT ck_document_blocks_heading_level "
        "CHECK (block_type = 'heading' OR heading_level IS NULL) NOT VALID"
    )

    # ── 8. sources — add updated_at + CHECK type↔field consistency ───────────
    op.add_column(
        "sources",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    _exec(
        "ALTER TABLE sources "
        "ADD CONSTRAINT ck_sources_type_field_consistency "
        "CHECK ("
        "  (type = 'url'  AND url IS NOT NULL) OR "
        "  (type = 'file' AND storage_key IS NOT NULL) OR "
        "  (type = 'text' AND text_content IS NOT NULL)"
        ") NOT VALID"
    )

    # ── 9. projects — add updated_at ─────────────────────────────────────────
    op.add_column(
        "projects",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


# ---------------------------------------------------------------------------
# downgrade
# ---------------------------------------------------------------------------


def downgrade() -> None:
    # 9
    op.drop_column("projects", "updated_at")

    # 8
    _exec("ALTER TABLE sources DROP CONSTRAINT IF EXISTS ck_sources_type_field_consistency")
    op.drop_column("sources", "updated_at")

    # 7
    _exec("ALTER TABLE document_blocks DROP CONSTRAINT IF EXISTS ck_document_blocks_heading_level")
    _exec(
        "ALTER INDEX IF EXISTS uq_document_blocks_doc_position "
        "RENAME TO ix_document_blocks_document_position"
    )
    op.drop_constraint("uq_document_blocks_doc_ref", "document_blocks", type_="unique")
    op.drop_column("document_blocks", "block_ref")

    # 6
    op.drop_column("suggestions", "order_index")
    _exec("ALTER TABLE suggestions DROP CONSTRAINT IF EXISTS suggestions_block_id_fkey")
    _exec("ALTER TABLE suggestions DROP CONSTRAINT IF EXISTS ck_suggestions_confidence_range")
    _exec(
        "ALTER TABLE suggestions "
        "ALTER COLUMN confidence_score TYPE FLOAT USING confidence_score::float"
    )

    # 5
    op.drop_index("ix_audit_logs_document_created", table_name="audit_logs")
    _exec("ALTER TABLE audit_logs DROP CONSTRAINT IF EXISTS ck_audit_logs_ref_not_null")
    _exec("ALTER TABLE audit_logs DROP CONSTRAINT IF EXISTS audit_logs_user_id_fkey")
    _exec(
        "ALTER TABLE audit_logs "
        "ADD CONSTRAINT audit_logs_user_id_fkey "
        "FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE"
    )
    op.drop_column("audit_logs", "details")

    # 4
    _exec("ALTER TABLE documents DROP CONSTRAINT IF EXISTS ck_documents_size_bytes_non_negative")
    op.drop_column("documents", "uploaded_at")
    op.drop_column("documents", "size_bytes")
    op.alter_column("documents", "name", new_column_name="title", type_=sa.String(500))
    _exec("ALTER TABLE documents DROP CONSTRAINT IF EXISTS documents_current_analysis_job_id_fkey")
    _exec(
        "ALTER TABLE documents "
        "ADD CONSTRAINT documents_current_analysis_job_id_fkey "
        "FOREIGN KEY (current_analysis_job_id) "
        "REFERENCES analysis_jobs(id) ON DELETE CASCADE"
    )
    _exec("ALTER TABLE documents ALTER COLUMN updated_at DROP NOT NULL")

    # 3 — restore surrogate id PK on document_opens
    _exec("ALTER TABLE document_opens DROP CONSTRAINT IF EXISTS document_opens_pkey")
    op.add_column(
        "document_opens",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.func.gen_random_uuid(),
        ),
    )
    _exec("ALTER TABLE document_opens ADD PRIMARY KEY (id)")
    op.create_unique_constraint(
        "uq_document_opens_user_document",
        "document_opens",
        ["user_id", "document_id"],
    )

    # 2 — restore surrogate id PK on document_sources
    _exec("ALTER TABLE document_sources DROP CONSTRAINT IF EXISTS document_sources_pkey")
    op.add_column(
        "document_sources",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.func.gen_random_uuid(),
        ),
    )
    _exec("ALTER TABLE document_sources ADD PRIMARY KEY (id)")

    # 1 — enum values cannot be removed in PostgreSQL without DROP TYPE + recreate;
    # downgrade leaves the new enum values in place (safe — no rows reference them).
