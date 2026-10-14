from app.domain.interfaces.document_parser import ParsedDocument
from app.domain.text_normalization import normalize_text


class TxtParser:
    def parse(self, raw_bytes: bytes) -> ParsedDocument:
        text = normalize_text(raw_bytes.decode("utf-8", errors="replace"))
        return ParsedDocument(plain_text=text, sections=[])
