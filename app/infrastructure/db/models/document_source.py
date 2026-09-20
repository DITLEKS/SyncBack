"""
Связующая таблица M:N между документами и источниками.
"""

import uuid

from sqlalchemy import Column, ForeignKey, Table, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID

from app.infrastructure.db.base import Base

# Единственная декларация промежуточной таблицы для M:N-связи Document↔Source.
# Другие модули (Document, Source, repositories) должны импортировать
# document_sources из этого файла и использовать его через secondary/document_sources,
# не создавая новый Table("document_sources", Base.metadata, ...) — иначе SQLAlchemy
# будет считать, что таблица объявлена дважды в одном MetaData.

document_sources = Table(
    "document_sources",
    Base.metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
    Column("document_id", UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
    Column("source_id", UUID(as_uuid=True), ForeignKey("sources.id", ondelete="CASCADE"), nullable=False),
    UniqueConstraint("document_id", "source_id", name="uq_document_sources_document_source"),
)
