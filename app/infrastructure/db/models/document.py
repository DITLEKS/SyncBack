import uuid

from sqlalchemy import Column, Enum, ForeignKey, Integer, Table
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.infrastructure.db.base import Base
from app.infrastructure.db.models.enums import DocumentFormat, DocumentStatus
from app.infrastructure.db.models.source_scope import SourceScope


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

    # P0-2: версия ревью для оптимистической блокировки.
    # Инкрементируется при каждом атомарном PUT /review.
    # Клиент обязан передать текущее значение; при расхождении — 409.
    review_version = Column(Integer, nullable=False, default=0, server_default="0")

    project = relationship("Project", back_populates="documents")
    sources = relationship("Source", secondary="document_sources", back_populates="documents")


document_sources = Table(
    "document_sources",
    Base.metadata,
    Column("document_id", UUID(as_uuid=True), ForeignKey("documents.id"), primary_key=True),
    Column("source_id", UUID(as_uuid=True), ForeignKey("sources.id"), primary_key=True),
)
