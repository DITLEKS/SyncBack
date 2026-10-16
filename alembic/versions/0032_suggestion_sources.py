"""Add many-to-many source references for suggestions.

Revision ID: 0032_suggestion_sources
Revises: 0031_analysis_job_sources_lock_token
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0032_suggestion_sources"
down_revision: str | None = "0031_analysis_job_sources_lock_token"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "suggestion_sources",
        sa.Column("suggestion_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["suggestion_id"], ["suggestions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("suggestion_id", "source_id"),
    )
    op.create_index(
        "ix_suggestion_sources_source_id", "suggestion_sources", ["source_id"]
    )

    connection = op.get_bind()
    columns = {row[1] for row in connection.execute(sa.text("PRAGMA table_info(suggestions)"))} if connection.dialect.name == "sqlite" else {row[0] for row in connection.execute(sa.text("SELECT column_name FROM information_schema.columns WHERE table_name = 'suggestions'"))}
    if "source_id" in columns:
        op.execute(
            sa.text(
                "INSERT INTO suggestion_sources (suggestion_id, source_id) "
                "SELECT id, source_id FROM suggestions WHERE source_id IS NOT NULL"
            )
        )
        op.drop_constraint("fk_suggestions_source_id", "suggestions", type_="foreignkey")
        op.drop_index("ix_suggestions_source_id", table_name="suggestions")
        op.drop_column("suggestions", "source_id")


def downgrade() -> None:
    op.add_column("suggestions", sa.Column("source_id", sa.Uuid(), nullable=True))
    op.create_index("ix_suggestions_source_id", "suggestions", ["source_id"])
    op.create_foreign_key(
        "fk_suggestions_source_id",
        "suggestions",
        "sources",
        ["source_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.execute(
        sa.text(
            "UPDATE suggestions SET source_id = ("
            "SELECT source_id FROM suggestion_sources "
            "WHERE suggestion_sources.suggestion_id = suggestions.id LIMIT 1)"
        )
    )
    op.drop_index("ix_suggestion_sources_source_id", table_name="suggestion_sources")
    op.drop_table("suggestion_sources")
