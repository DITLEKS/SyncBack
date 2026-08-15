from pathlib import Path

from app.domain.interfaces.document_parser import ParsedDocument
from app.infrastructure.parsers.docx_parser import DocxParser
from app.infrastructure.parsers.markdown_parser import MarkdownParser
from app.infrastructure.parsers.txt_parser import TxtParser


class DocumentParserRegistry:
    def __init__(self):
        self._docx_parser = DocxParser()
        self._text_parser = TxtParser()
        self._markdown_parser = MarkdownParser()

    def parse_by_filename(self, filename: str, raw_bytes: bytes) -> ParsedDocument:
        suffix = Path(filename).suffix.lower()
        if suffix in (".docx", ".doc"):
            return self._docx_parser.parse(raw_bytes)
        if suffix in (".md", ".markdown"):
            return self._markdown_parser.parse(raw_bytes)
        return self._text_parser.parse(raw_bytes)
