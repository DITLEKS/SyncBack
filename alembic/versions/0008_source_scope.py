"""
P0-6: добавляет колонку scope в таблицу sources.

SourceScope.PROJECT  — источник применяется ко всем документам проекта (default).
SourceScope.DOCUMENT — источник привязан к конкретному документу.

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-20
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

SOURCE_SCOPE_ENUM = postgresql.ENUM("project", "document", name="source_scope")


def upgrade() -> None:
    bind = op.get_bind()
    SOURCE_SCOPE_ENUM.create(bind, checkfirst=True)

    op.add_column(
        "sources",
        sa.Column(
            "scope",
            postgresql.ENUM("project", "document", name="source_scope", create_type=False),
            nullable=False,
            server_default="project",
        ),
    )
    op.create_index("ix_sources_scope", "sources", ["scope"])


def downgrade() -> None:
    op.drop_index("ix_sources_scope", table_name="sources")
    op.drop_column("sources", "scope")

    bind = op.get_bind()
    SOURCE_SCOPE_ENUM.drop(bind, checkfirst=True)
