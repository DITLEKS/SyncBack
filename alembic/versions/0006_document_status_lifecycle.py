"""document status lifecycle

Revision ID: 0006_document_status_lifecycle
Revises: 0005_project_description
"""

from alembic import op
import sqlalchemy as sa

revision = "0006_document_status_lifecycle"
down_revision = "0005_project_description"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("target_status", sa.Text(), nullable=True))
    op.execute("""UPDATE documents d SET target_status = CASE
      WHEN d.status::text IN ('uploaded','error') THEN 'draft'
      WHEN d.status::text = 'analyzing' AND EXISTS (SELECT 1 FROM analysis_jobs aj WHERE aj.document_id=d.id AND aj.status::text IN ('pending','processing')) THEN 'in_progress'
      WHEN d.status::text = 'analyzing' THEN 'draft'
      WHEN d.status::text = 'analyzed' AND EXISTS (SELECT 1 FROM suggestions s WHERE s.analysis_job_id=d.current_analysis_job_id AND s.status::text='pending') THEN 'awaiting_approval'
      ELSE 'ready' END""")
    op.execute("ALTER TABLE documents ALTER COLUMN status DROP DEFAULT")
    op.execute("ALTER TYPE document_status RENAME TO document_status_old")
    op.execute("CREATE TYPE document_status AS ENUM ('draft','in_progress','awaiting_approval','ready')")
    op.execute(
        "ALTER TABLE documents ALTER COLUMN status TYPE document_status USING target_status::document_status"
    )
    op.drop_column("documents", "target_status")
    op.execute("ALTER TABLE documents ALTER COLUMN status SET DEFAULT 'draft'")
    op.execute("DROP TYPE document_status_old")
    op.execute("ALTER TYPE analysis_job_status ADD VALUE IF NOT EXISTS 'cancelled'")
    op.execute(
        """WITH ranked AS (SELECT id,row_number() OVER(PARTITION BY document_id ORDER BY created_at DESC,id DESC) pos FROM analysis_jobs WHERE status::text IN ('pending','processing')) UPDATE analysis_jobs aj SET status='failed',error_code='SUPERSEDED_BY_MIGRATION',error_message='Задача закрыта при миграции статусной модели',finished_at=now() FROM ranked r WHERE aj.id=r.id AND r.pos>1"""
    )
    op.create_index(
        "uq_analysis_jobs_one_active_per_document",
        "analysis_jobs",
        ["document_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending','processing')"),
    )


def downgrade() -> None:
    op.drop_index("uq_analysis_jobs_one_active_per_document", table_name="analysis_jobs")
    op.execute("UPDATE analysis_jobs SET status='failed' WHERE status='cancelled'")
    op.execute("ALTER TABLE analysis_jobs ALTER COLUMN status DROP DEFAULT")
    op.execute("ALTER TYPE analysis_job_status RENAME TO analysis_job_status_old")
    op.execute("CREATE TYPE analysis_job_status AS ENUM ('pending','processing','success','failed')")
    op.execute(
        "ALTER TABLE analysis_jobs ALTER COLUMN status TYPE analysis_job_status USING status::text::analysis_job_status"
    )
    op.execute("ALTER TABLE analysis_jobs ALTER COLUMN status SET DEFAULT 'pending'")
    op.execute("DROP TYPE analysis_job_status_old")
    op.add_column("documents", sa.Column("target_status", sa.Text(), nullable=True))
    op.execute(
        "UPDATE documents SET target_status=CASE status::text WHEN 'draft' THEN 'uploaded' WHEN 'in_progress' THEN 'analyzing' ELSE 'analyzed' END"
    )
    op.execute("ALTER TABLE documents ALTER COLUMN status DROP DEFAULT")
    op.execute("ALTER TYPE document_status RENAME TO document_status_new")
    op.execute("CREATE TYPE document_status AS ENUM ('uploaded','analyzing','analyzed','error')")
    op.execute(
        "ALTER TABLE documents ALTER COLUMN status TYPE document_status USING target_status::document_status"
    )
    op.drop_column("documents", "target_status")
    op.execute("ALTER TABLE documents ALTER COLUMN status SET DEFAULT 'uploaded'")
    op.execute("DROP TYPE document_status_new")
