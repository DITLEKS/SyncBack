from app.domain.interfaces.document_parser import ParsedDocument


class TxtParser:
    def parse(self, raw_bytes: bytes) -> ParsedDocument:
        text = raw_bytes.decode("utf-8", errors="replace")
        return ParsedDocument(plain_text=text, sections=[])
