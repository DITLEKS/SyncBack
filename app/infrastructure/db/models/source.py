"""Расширение модели Source для разделения базовых и специфичных источников.

SourceScope.PROJECT  — базовые источники проекта, применяются ко всем документам.
SourceScope.DOCUMENT — специфичные источники, применяются только к одному документу.
"""
import uuid

from sqlalchemy import Column, Enum, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.infrastructure.db.base import Base
from app.infrastructure.db.models.enums import SourceType
from app.infrastructure.db.models.source_scope import SourceScope


class Source(Base):
    __tablename__ = "sources"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id"), nullable=False)
    name = Column("name", nullable=False)
    type = Column(Enum(SourceType, name="source_type"), nullable=False)
    storage_key = Column("storage_key")
    text_content = Column("text_content")
    url = Column("url")
    uploaded_at = Column("uploaded_at")
    scope = Column(Enum(SourceScope, name="source_scope"), nullable=False, default=SourceScope.PROJECT)

    project = relationship("Project", back_populates="sources")
    documents = relationship("Document", secondary="document_sources", back_populates="sources")
