from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class DocumentSection:
    ref: str
    start_offset: int
    end_offset: int


@dataclass
class ParsedDocument:
    plain_text: str
    sections: list[DocumentSection] = field(default_factory=list)


class DocumentParser(Protocol):
    def parse(self, raw_bytes: bytes) -> ParsedDocument: ...
