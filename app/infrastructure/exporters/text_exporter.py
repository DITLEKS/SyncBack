"""
Применяет принятые правки к txt/markdown простой текстовой заменой. Посимвольный diff
сознательно не делаем на этой итерации.

Добавление (add) вставляется по месту, а не в конец файла:
  1. есть якорь old_text и он найден — новый абзац сразу после строки с якорем;
  2. иначе section_ref совпадает с заголовком — в конец этого раздела (перед следующим заголовком);
  3. иначе — в конец документа (прежнее поведение).
Тот же порядок повторяет фронтенд в «Чистовике», чтобы экран и экспорт не расходились.
"""

from app.domain.interfaces.document_exporter import AppliedChange
from app.infrastructure.parsers.markdown_parser import MarkdownParser


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


def _section_end(text: str, section_ref: str) -> int | None:
    """Граница раздела по заголовку — тем же парсером, что считал разделы при загрузке."""
    ref = section_ref.strip().casefold()
    if not ref:
        return None
    for section in MarkdownParser().parse(text.encode("utf-8")).sections:
        if section.ref.casefold() == ref:
            return section.end_offset
    return None


def place_addition(text: str, change: AppliedChange) -> str:
    block = (change.new_text or "").strip("\n")
    anchor = (change.old_text or "").strip()
    if anchor:
        position = _anchor_line_end(text, anchor)
        if position is not None:
            return _insert_block(text, position, block)
    position = _section_end(text, change.section_ref)
    if position is not None:
        return _insert_block(text, position, block)
    return _insert_block(text, len(text), block)


class TextExporter:
    def apply_changes(self, raw_bytes: bytes, changes: list[AppliedChange]) -> bytes:
        text = raw_bytes.decode("utf-8", errors="replace")
        for change in changes:
            if change.change_type == "modify" and change.old_text:
                text = text.replace(change.old_text, change.new_text or "", 1)
            elif change.change_type == "delete" and change.old_text:
                text = text.replace(change.old_text, "", 1)
            elif change.change_type == "add" and change.new_text:
                text = place_addition(text, change)
        return text.encode("utf-8")
