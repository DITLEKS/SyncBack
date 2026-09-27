"""
ОRM-модель для трекинга последнего открытия документа пользователем.
PK — составной (user_id, document_id) для эффективного upsert:
  INSERT ... ON CONFLICT (user_id, document_id) DO UPDATE SET last_opened_at = now()
"""
from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.db.base import Base


class DocumentOpen(Base):
    __tablename__ = "document_opens"

    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        sa.UUID(as_uuid=True),
        sa.ForeignKey("documents.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    last_opened_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
    )
