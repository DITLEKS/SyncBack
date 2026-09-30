"""Add 'editor' and 'viewer' to user_role enum.

Revision ID: 0024
Revises: 0023
Create Date: 2026-09-30

Changes:
  - ALTER TYPE user_role ADD VALUE IF NOT EXISTS 'editor'
  - ALTER TYPE user_role ADD VALUE IF NOT EXISTS 'viewer'

ADD VALUE runs outside a transaction block (PostgreSQL requirement for enum
value additions on PG < 12; on PG 12+ it is allowed inside a transaction but
the new value is not visible until commit — using op.execute directly avoids
Alembic wrapping the statement in a subtransaction).

downgrade: UPDATE rows using 'editor'/'viewer' back to 'user', then drop the
values. PostgreSQL 15+ supports ALTER TYPE ... DROP VALUE; on older versions
the downgrade will fail if any rows still use the removed values — this is
intentional (data safety guard).
"""
from __future__ import annotations

from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ADD VALUE is a DDL statement that commits immediately in PostgreSQL;
    # IF NOT EXISTS makes it safe to re-run.
    op.execute("ALTER TYPE user_role ADD VALUE IF NOT EXISTS 'editor'")
    op.execute("ALTER TYPE user_role ADD VALUE IF NOT EXISTS 'viewer'")


def downgrade() -> None:
    # Reassign rows before removing the enum values (PG 15+ only).
    op.execute("UPDATE users SET role = 'user' WHERE role IN ('editor', 'viewer')")
    op.execute("ALTER TYPE user_role DROP VALUE IF EXISTS 'viewer'")
    op.execute("ALTER TYPE user_role DROP VALUE IF EXISTS 'editor'")
