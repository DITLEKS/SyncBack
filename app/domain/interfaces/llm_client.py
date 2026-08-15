from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class LLMSuggestionItem:
    section_ref: str
    change_type: str
    old_text: str | None
    new_text: str | None


@dataclass
class LLMSuggestionBatch:
    items: list[LLMSuggestionItem] = field(default_factory=list)
    raw_response: str = ""


class LLMClient(Protocol):
    async def generate_suggestions(self, document_text: str, source_text: str, document_format: str) -> LLMSuggestionBatch: ...
    async def health_check(self) -> bool: ...
