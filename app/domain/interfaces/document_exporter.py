"""
Порт для сборки финального документа с учётом принятых правок. Работает с лёгким DTO
AppliedChange, а не с ORM-моделью Suggestion напрямую.
"""
from dataclasses import dataclass
from typing import Protocol


@dataclass
class AppliedChange:
    section_ref: str
    change_type: str
    old_text: str | None
    new_text: str | None


class DocumentExporter(Protocol):
    def apply_changes(self, raw_bytes: bytes, changes: list[AppliedChange]) -> bytes: ...
