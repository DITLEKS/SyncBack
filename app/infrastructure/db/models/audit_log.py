import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy import DateTime, Index, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.db.base import Base
from app.infrastructure.db.models.enums import AuditAction

_values = lambda e: [m.value for m in e]  # noqa: E731


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        # Хотя бы одна из двух ссылок должна быть заполнена.
        sa.CheckConstraint(
            "suggestion_id IS NOT NULL OR document_id IS NOT NULL",
            name="ck_audit_logs_ref_not_null",
        ),
        # Составной индекс для запросов по документу в хронологии.
        Index("ix_audit_logs_document_created", "document_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    suggestion_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("suggestions.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    # SET NULL: удаление пользователя НЕ должно уничтожать аудит-лог.
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    action: Mapped[AuditAction] = mapped_column(
        sa.Enum(AuditAction, name="audit_action", values_callable=_values),
        nullable=False,
    )
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
