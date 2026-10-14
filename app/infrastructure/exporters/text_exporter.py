"""
Применяет принятые правки к txt/markdown простой текстовой заменой. Посимвольный diff
сознательно не делаем на этой итерации.

Добавление (add) вставляется только по якорю: новый абзац встаёт сразу после строки
с первым вхождением old_text. Якоря нет или он не найден — правка не применяется,
как modify/delete с ненайденным old_text; место вставки за модель не выбираем.
Тот же порядок повторяет фронтенд в «Чистовике», чтобы экран и экспорт не расходились.

Текст файла и фрагменты правок перед сравнением нормализуются (см. normalize_text):
модель возвращает обычные пробелы там, где в файле неразрывные. Побочный эффект —
в экспортированном файле NBSP заменяются на обычные пробелы.
"""

from app.domain.interfaces.document_exporter import AppliedChange
from app.domain.text_normalization import normalize_text


def _insert_block(text: str, position: int, block: str) -> str:
    """Вставить абзац в позицию, оставив ровно одну пустую строку вокруг него."""
    head = text[:position].rstrip("\n")
    tail = text[position:].lstrip("\n")
    parts = [head] if head else []
    parts.append(block)
    joined = "\n\n".join(parts)
    return joined + ("\n\n" + tail if tail else "\n")


def _anchor_line_end(text: str, anchor: str) -> int | None:
    """Конец строки, в которой заканчивается первое вхождение якоря; None, если якоря нет."""
    index = text.find(anchor)
    if index < 0:
        return None
    newline = text.find("\n", index + len(anchor))
    return len(text) if newline < 0 else newline


def place_addition(text: str, change: AppliedChange) -> str:
    """Вставить добавление после строки с якорем; без найденного якоря вернуть текст как есть."""
    anchor = normalize_text(change.old_text or "").strip()
    if not anchor:
        return text
    position = _anchor_line_end(text, anchor)
    if position is None:
        return text
    return _insert_block(text, position, (change.new_text or "").strip("\n"))


class TextExporter:
    def apply_changes(self, raw_bytes: bytes, changes: list[AppliedChange]) -> bytes:
        text = normalize_text(raw_bytes.decode("utf-8", errors="replace"))
        for change in changes:
            old_text = normalize_text(change.old_text or "")
            if change.change_type == "modify" and old_text:
                text = text.replace(old_text, change.new_text or "", 1)
            elif change.change_type == "delete" and old_text:
                text = text.replace(old_text, "", 1)
            elif change.change_type == "add" and change.new_text:
                text = place_addition(text, change)
        return text.encode("utf-8")
