"""Document ORM model with optimistic review versioning.

LOW: format column now uses DocumentFormatVO from domain/value_objects
(imported via the backward-compat alias in enums.py). The SQLAlchemy
Enum name stays 'document_format' so no migration is needed.
"""

import uuid

from sqlalchemy import Column, Enum, ForeignKey, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.infrastructure.db.base import Base
# DocumentFormat is now DocumentFormatVO re-exported from domain.value_objects
from app.infrastructure.db.models.enums import DocumentFormat, DocumentStatus


class Document(Base):
    __tablename__ = "documents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id"), nullable=False)
    name = Column("name", nullable=False)
    format = Column(
        Enum(
            DocumentFormat,
            name="document_format",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )
    storage_key = Column("storage_key", nullable=False)
    size_bytes = Column("size_bytes", nullable=False)
    uploaded_at = Column("uploaded_at", nullable=False)
    status = Column(
        Enum(DocumentStatus, name="document_status"),
        nullable=False,
        default=DocumentStatus.DRAFT,
    )
    current_analysis_job_id = Column(UUID(as_uuid=True), ForeignKey("analysis_jobs.id"))
    review_version = Column(Integer, nullable=False, default=0, server_default="0")

    project = relationship("Project", back_populates="documents")
    sources = relationship("Source", secondary="document_sources", back_populates="documents")
    analysis_jobs = relationship(
        "AnalysisJob",
        back_populates="document",
        foreign_keys="AnalysisJob.document_id",
        cascade="all, delete-orphan",
    )
