import uuid

from sqlalchemy import CheckConstraint, Column, DateTime, Enum, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.infrastructure.db.base import Base
from app.infrastructure.db.models.enums import SourceType
from app.infrastructure.db.models.source_scope import SourceScope


class Source(Base):
    __tablename__ = "sources"
    __table_args__ = (
        # После миграции 0018 SourceType содержит только FILE и URL.
        # Ветка type='text' удалена — text_content более не используется.
        CheckConstraint(
            """
            (type = 'url'  AND url IS NOT NULL) OR
            (type = 'file' AND storage_key IS NOT NULL)
            """,
            name="ck_sources_type_field_consistency",
        ),
        Index("ix_sources_scope", "scope"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name = Column(String(255), nullable=False)
    type = Column(
        Enum(SourceType, name="source_type", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
    )
    storage_key = Column(String(1024), nullable=True)
    url = Column(String(2048), nullable=True)
    # R-4: uploaded_at удалён — дублировал created_at (server_default=func.now()).
    # list_by_project теперь сортирует по created_at DESC.
    scope = Column(
        Enum(SourceScope, name="source_scope", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
        default=SourceScope.PROJECT,
        server_default=SourceScope.PROJECT.value,
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    project = relationship("Project", back_populates="sources")
    documents = relationship("Document", secondary="document_sources", back_populates="sources")
