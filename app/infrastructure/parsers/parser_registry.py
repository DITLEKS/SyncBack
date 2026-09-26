from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Callable

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

# Суффикс, который читается как заблокированный (UnsupportedFormatError, а не fallback).
_BLOCKED_EXTENSIONS: frozenset[str] = frozenset({".doc"})


class DocumentParserRegistry:
    """Registry парсеров документов.

    Диспатчер суффиксов строится один раз через @lru_cache.
    Добавление нового формата: добавить парсер в __init__ и запись в _EXTENSION_MAP.
    """

    # Публичный маппинг суффиксов — дополнять в подклассах или при регистрации новых парсеров.
    # Ключ — нижний регистр суффикса с точкой (`.docx`), значение — атрибут имени парсера (str).
    _EXTENSION_MAP: dict[str, str] = {
        ".docx": "_docx_parser",
        ".md":   "_markdown_parser",
        ".markdown": "_markdown_parser",
    }
    # Отсутствующие суффиксы → fallback на TxtParser.

    def __init__(self) -> None:
        self._docx_parser     = DocxParser()
        self._txt_parser      = TxtParser()
        self._markdown_parser = MarkdownParser()

    @lru_cache(maxsize=None)
    def _get_parser_for_suffix(self, suffix: str) -> str | None:
        """Вернуть атрибут парсера для заданного суффикса или None (fallback).

        @lru_cache гарантирует O(1) dict.get() вместо O(n) if/elif-цепочки
        и кэширует результат на весь лифтайм объекта.
        """
        return self._EXTENSION_MAP.get(suffix)

    def parse_by_filename(self, filename: str, raw_bytes: bytes) -> ParsedDocument:
        suffix = Path(filename).suffix.lower()

        if suffix in _BLOCKED_EXTENSIONS:
            raise UnsupportedFormatError(
                ".doc (Word 97-2003) не поддерживается. "
                "Пожалуйста, конвертируйте файл в .docx перед загрузкой."
            )

        parser_attr = self._get_parser_for_suffix(suffix)
        parser = getattr(self, parser_attr) if parser_attr else self._txt_parser
        return parser.parse(raw_bytes)
