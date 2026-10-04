"""Заголовок Content-Disposition для скачивания файлов."""

import re
import unicodedata
from urllib.parse import quote

_UNSAFE_ASCII_RE = re.compile(r"[^A-Za-z0-9._ -]")


def content_disposition(filename: str) -> str:
    """Заголовок Content-Disposition с именем файла по RFC 6266.

    HTTP-заголовки передаются в latin-1, поэтому имя идёт дважды: ASCII-вариант
    для старых клиентов и UTF-8 в filename*. Кавычки и управляющие символы
    в ASCII-вариант не попадают.
    """
    ascii_name = unicodedata.normalize("NFKD", filename).encode("ascii", "ignore").decode()
    ascii_name = _UNSAFE_ASCII_RE.sub("_", ascii_name).strip() or "download"
    utf8_name = quote(filename, safe="")
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{utf8_name}"
