"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-08-09

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    user_role = postgresql.ENUM("admin", "user", name="user_role")
    user_role_column = postgresql.ENUM("admin", "user", name="user_role", create_type=False)
    document_format = postgresql.ENUM("doc", "docx", "txt", "markdown", name="document_format")
    document_format_column = postgresql.ENUM("doc", "docx", "txt", "markdown", name="document_format", create_type=False)
    document_status = postgresql.ENUM("uploaded", "analyzing", "analyzed", "error", name="document_status")
    document_status_column = postgresql.ENUM("uploaded", "analyzing", "analyzed", "error", name="document_status", create_type=False)
    source_type = postgresql.ENUM("file", "note", "link", name="source_type")
    source_type_column = postgresql.ENUM("file", "note", "link", name="source_type", create_type=False)
    analysis_job_status = postgresql.ENUM("pending", "processing", "success", "failed", name="analysis_job_status")
    analysis_job_status_column = postgresql.ENUM("pending", "processing", "success", "failed", name="analysis_job_status", create_type=False)
    change_type = postgresql.ENUM("add", "modify", "delete", name="change_type")
    change_type_column = postgresql.ENUM("add", "modify", "delete", name="change_type", create_type=False)
    suggestion_status = postgresql.ENUM("pending", "accepted", "rejected", name="suggestion_status")
    suggestion_status_column = postgresql.ENUM("pending", "accepted", "rejected", name="suggestion_status", create_type=False)
    audit_action = postgresql.ENUM("accept", "reject", name="audit_action")
    audit_action_column = postgresql.ENUM("accept", "reject", name="audit_action", create_type=False)

    bind = op.get_bind()
    for enum_type in (
        user_role, document_format, document_status, source_type,
        analysis_job_status, change_type, suggestion_status, audit_action,
    ):
        enum_type.create(bind, checkfirst=True)

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", user_role_column, nullable=False, server_default="user"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_users_email", "users", ["email"])

    op.create_table(
        "projects",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_projects_owner_id", "projects", ["owner_id"])

    op.create_table(
        "sources",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("type", source_type_column, nullable=False),
        sa.Column("storage_key", sa.String(1024), nullable=True),
        sa.Column("text_content", sa.Text(), nullable=True),
        sa.Column("url", sa.String(2048), nullable=True),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_sources_project_id", "sources", ["project_id"])

    op.create_table(
        "documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("format", document_format_column, nullable=False),
        sa.Column("storage_key", sa.String(1024), nullable=False),
        sa.Column("status", document_status_column, nullable=False, server_default="uploaded"),
        sa.Column("current_analysis_job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_documents_project_id", "documents", ["project_id"])

    op.create_table(
        "document_sources",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("sources.id", ondelete="CASCADE"), nullable=False),
        sa.UniqueConstraint("document_id", "source_id", name="uq_document_sources_document_source"),
    )

    op.create_table(
        "analysis_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", analysis_job_status_column, nullable=False, server_default="pending"),
        sa.Column("celery_task_id", sa.String(255), nullable=True),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_analysis_jobs_document_id", "analysis_jobs", ["document_id"])

    op.create_foreign_key(
        "fk_documents_current_analysis_job",
        "documents", "analysis_jobs",
        ["current_analysis_job_id"], ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "suggestions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("analysis_job_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("analysis_jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("section_ref", sa.String(500), nullable=False),
        sa.Column("change_type", change_type_column, nullable=False),
        sa.Column("old_text", sa.Text(), nullable=True),
        sa.Column("new_text", sa.Text(), nullable=True),
        sa.Column("status", suggestion_status_column, nullable=False, server_default="pending"),
        sa.Column("source_reference", sa.String(500), nullable=True),
        sa.Column("confidence_score", sa.Float(), nullable=True),
        sa.Column("explanation", sa.Text(), nullable=True),
        sa.Column("decided_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_suggestions_analysis_job_id", "suggestions", ["analysis_job_id"])

    op.create_table(
        "audit_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("suggestion_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("suggestions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("action", audit_action_column, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_audit_logs_suggestion_id", "audit_logs", ["suggestion_id"])
    op.create_index("ix_audit_logs_user_id", "audit_logs", ["user_id"])


def downgrade() -> None:
    op.drop_table("audit_logs")
    op.drop_table("suggestions")
    op.drop_constraint("fk_documents_current_analysis_job", "documents", type_="foreignkey")
    op.drop_table("analysis_jobs")
    op.drop_table("document_sources")
    op.drop_table("documents")
    op.drop_table("sources")
    op.drop_table("projects")
    op.drop_table("users")

    bind = op.get_bind()
    for enum_name in (
        "audit_action", "suggestion_status", "change_type", "analysis_job_status",
        "source_type", "document_status", "document_format", "user_role",
    ):
        postgresql.ENUM(name=enum_name).drop(bind, checkfirst=True)
