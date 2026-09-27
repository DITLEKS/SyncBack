import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, Column, DateTime, Enum, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.infrastructure.db.base import Base
from app.infrastructure.db.models.enums import DocumentFormat, DocumentStatus


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint("size_bytes >= 0", name="ck_documents_size_bytes_non_negative"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name = Column(String(512), nullable=False)
    format = Column(
        Enum(
            DocumentFormat,
            name="document_format",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )
    storage_key = Column(String(1024), nullable=False)
    size_bytes = Column(Integer, nullable=False)
    uploaded_at = Column(DateTime(timezone=True), nullable=False)
    status = Column(
        Enum(
            DocumentStatus,
            name="document_status",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=DocumentStatus.DRAFT,
        server_default=DocumentStatus.DRAFT.value,
    )
    # SET NULL: удаление job не должно каскадно удалять документ.
    current_analysis_job_id = Column(
        UUID(as_uuid=True),
        ForeignKey("analysis_jobs.id", ondelete="SET NULL"),
        nullable=True,
    )
    review_version = Column(Integer, nullable=False, default=0, server_default="0")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    project = relationship("Project", back_populates="documents")
    sources = relationship("Source", secondary="document_sources", back_populates="documents")
    analysis_jobs = relationship(
        "AnalysisJob",
        back_populates="document",
        foreign_keys="AnalysisJob.document_id",
        cascade="all, delete-orphan",
    )
