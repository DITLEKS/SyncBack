"""
Порты для сборки финального документа с учётом принятых правок. Работают с лёгким DTO
AppliedChange, а не с ORM-моделью Suggestion напрямую.
"""

from dataclasses import dataclass
from typing import Protocol

from app.domain.value_objects import DocumentFormatVO


@dataclass
class AppliedChange:
    section_ref: str
    change_type: str
    old_text: str | None
    new_text: str | None


class DocumentExporter(Protocol):
    def apply_changes(self, raw_bytes: bytes, changes: list[AppliedChange]) -> bytes: ...


class DocumentExporterRegistry(Protocol):
    def get_exporter(self, document_format: DocumentFormatVO) -> DocumentExporter: ...
