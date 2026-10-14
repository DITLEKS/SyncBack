"""Приведение текста документа к виду, устойчивому к невидимым различиям.

LLM при копировании фрагмента (old_text) заменяет неразрывные и прочие «особые» пробелы
на обычные, а разложенные Unicode-формы — на составные. Чтобы old_text совпадал
с документом дословно, парсеры нормализуют plain_text один раз на входе, а экспортёры
сравнивают с текстом файла только после той же нормализации.
"""

import unicodedata

_SPACE_LIKE = str.maketrans(
    {
        "\u00a0": " ",  # NO-BREAK SPACE
        "\u202f": " ",  # NARROW NO-BREAK SPACE
        "\u2007": " ",  # FIGURE SPACE
        "\u2009": " ",  # THIN SPACE
        "\u200b": None,  # ZERO WIDTH SPACE
        "\ufeff": None,  # BOM / ZERO WIDTH NO-BREAK SPACE
    }
)


def normalize_text(text: str) -> str:
    """Нормализовать текст документа или фрагмента правки."""
    return unicodedata.normalize("NFC", text).replace("\r\n", "\n").translate(_SPACE_LIKE)
