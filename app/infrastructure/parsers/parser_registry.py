from pathlib import Path

from app.domain.exceptions import UnsupportedFormatError
from app.domain.interfaces.document_parser import ParsedDocument
from app.infrastructure.parsers.docx_parser import DocxParser
from app.infrastructure.parsers.markdown_parser import MarkdownParser
from app.infrastructure.parsers.txt_parser import TxtParser

# Поддерживаемые форматы:
#   .docx  — Office OpenXML (python-docx)
#   .md / .markdown — Markdown
#   .txt и всё остальное — plain text
#
# .doc (OLE2 / Word 97-2003) НЕ поддерживается: python-docx работает только
# с .docx. Попытка открыть .doc через DocxDocument вызывает PackageNotFoundError.
# Клиент должен предварительно конвертировать файл в .docx.


class DocumentParserRegistry:
    def __init__(self):
        self._docx_parser = DocxParser()
        self._text_parser = TxtParser()
        self._markdown_parser = MarkdownParser()

    def parse_by_filename(self, filename: str, raw_bytes: bytes) -> ParsedDocument:
        suffix = Path(filename).suffix.lower()
        if suffix == ".doc":
            raise UnsupportedFormatError(
                ".doc (Word 97-2003) не поддерживается. "
                "Пожалуйста, конвертируйте файл в .docx перед загрузкой."
            )
        if suffix == ".docx":
            return self._docx_parser.parse(raw_bytes)
        if suffix in (".md", ".markdown"):
            return self._markdown_parser.parse(raw_bytes)
        return self._text_parser.parse(raw_bytes)
