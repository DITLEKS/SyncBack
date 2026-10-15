import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.value_objects import SourceScopeVO
from app.infrastructure.db.base import Base
from app.infrastructure.db.models.enums import SourceType

if TYPE_CHECKING:
    from app.infrastructure.db.models.document import Document
    from app.infrastructure.db.models.project import Project


class Source(Base):
    __tablename__ = "sources"
    __table_args__ = (
        CheckConstraint(
            """
            (type = 'url'  AND url IS NOT NULL) OR
            (type = 'file' AND storage_key IS NOT NULL)
            """,
            name="ck_sources_type_field_consistency",
        ),
        Index("ix_sources_scope", "scope"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[SourceType] = mapped_column(
        Enum(SourceType, name="source_type", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
    )
    storage_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    # Область видимости — доменный VO напрямую: у scope нет инфраструктурных
    # значений, которых не было бы в домене, поэтому отдельный ORM-enum не нужен.
    scope: Mapped[SourceScopeVO] = mapped_column(
        Enum(SourceScopeVO, name="source_scope", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
        default=SourceScopeVO.PROJECT,
        server_default=SourceScopeVO.PROJECT.value,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    project: Mapped["Project"] = relationship("Project", back_populates="sources")
    documents: Mapped[list["Document"]] = relationship(
        "Document", secondary="document_sources", back_populates="sources"
    )
