"""
P0-2: добавлена колонка review_version (Integer, default=0) для
оптимистической блокировки PUT /review (If-Match / ETag).
"""
import uuid

from sqlalchemy import Column, Enum, ForeignKey, Integer, Table
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.infrastructure.db.base import Base
from app.infrastructure.db.models.enums import DocumentFormat, DocumentStatus


class Document(Base):
    __tablename__ = "documents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id"), nullable=False)
    name = Column("name", nullable=False)
    format = Column(Enum(DocumentFormat, name="document_format"), nullable=False)
    storage_key = Column("storage_key", nullable=False)
    size_bytes = Column("size_bytes", nullable=False)
    uploaded_at = Column("uploaded_at", nullable=False)
    status = Column(Enum(DocumentStatus, name="document_status"), nullable=False, default=DocumentStatus.DRAFT)
    current_analysis_job_id = Column(UUID(as_uuid=True), ForeignKey("analysis_jobs.id"))
    # P0-2: версия review для оптимистической блокировки.
    # Инкрементируется при каждом вызове PUT /review (finalize_review).
    # Клиент передаёт текущее значение в заголовке If-Match; при несовпадении → 412.
    review_version = Column(Integer, nullable=False, default=0, server_default="0")

    project = relationship("Project", back_populates="documents")
    sources = relationship("Source", secondary="document_sources", back_populates="documents")


document_sources = Table(
    "document_sources",
    Base.metadata,
    Column("document_id", UUID(as_uuid=True), ForeignKey("documents.id"), primary_key=True),
    Column("source_id", UUID(as_uuid=True), ForeignKey("sources.id"), primary_key=True),
)
