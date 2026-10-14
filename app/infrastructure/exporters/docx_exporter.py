"""
Применяет принятые правки к docx: абзац с вхождением old_text переписывается целиком
(все runs очищаются и заменяются одним run с новым текстом — внутреннее форматирование
изменённого абзаца не сохраняется, это упрощение MVP).

Добавление (add) вставляется только по якорю — новым абзацем сразу после абзаца с первым
вхождением old_text, как в TextExporter. Якоря нет или он не найден — правка не применяется;
место вставки за модель не выбираем.
"""

import io

from docx import Document as DocxDocument

from app.domain.interfaces.document_exporter import AppliedChange


def _set_paragraph_text(paragraph, new_text: str) -> None:
    for run in paragraph.runs:
        run.text = ""
    if paragraph.runs:
        paragraph.runs[0].text = new_text
    else:
        paragraph.add_run(new_text)


def _insert_paragraph_after(paragraph, text: str):
    # python-docx умеет только insert_paragraph_before; переставляем созданный элемент за якорь.
    new_paragraph = paragraph.insert_paragraph_before(text)
    paragraph._p.addnext(new_paragraph._p)
    return new_paragraph


def _place_addition(document, change: AppliedChange) -> None:
    anchor = (change.old_text or "").strip()
    if not anchor:
        return
    for paragraph in document.paragraphs:
        if anchor in paragraph.text:
            _insert_paragraph_after(paragraph, change.new_text or "")
            return


class DocxExporter:
    def apply_changes(self, raw_bytes: bytes, changes: list[AppliedChange]) -> bytes:
        document = DocxDocument(io.BytesIO(raw_bytes))
        for change in changes:
            if change.change_type in ("modify", "delete") and change.old_text:
                for paragraph in document.paragraphs:
                    if change.old_text in paragraph.text:
                        new_text = paragraph.text.replace(change.old_text, change.new_text or "", 1)
                        _set_paragraph_text(paragraph, new_text)
                        break
            elif change.change_type == "add" and change.new_text:
                _place_addition(document, change)
        buffer = io.BytesIO()
        document.save(buffer)
        return buffer.getvalue()
