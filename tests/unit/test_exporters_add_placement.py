"""Принятые добавления вставляются по месту: якорь → раздел → конец документа."""

import io

from docx import Document as DocxDocument

from app.domain.interfaces.document_exporter import AppliedChange
from app.infrastructure.exporters.docx_exporter import DocxExporter
from app.infrastructure.exporters.text_exporter import TextExporter

MD = (
    "# Сроки\n"
    "Срок ответа — 10 дней.\n"
    "Оплата картой.\n"
    "\n"
    "# Ответственность\n"
    "Стороны несут ответственность.\n"
)


def _add(new_text: str, *, anchor: str | None = None, section: str = "") -> AppliedChange:
    return AppliedChange(section_ref=section, change_type="add", old_text=anchor, new_text=new_text)


def _apply_text(changes: list[AppliedChange], text: str = MD) -> str:
    return TextExporter().apply_changes(text.encode("utf-8"), changes).decode("utf-8")


# ── txt / markdown ────────────────────────────────────────────────────────────────────────────────


def test_text_add_goes_right_after_anchor_line() -> None:
    result = _apply_text([_add("Срок может быть продлён.", anchor="Срок ответа — 10 дней.")])
    assert result == (
        "# Сроки\n"
        "Срок ответа — 10 дней.\n"
        "\n"
        "Срок может быть продлён.\n"
        "\n"
        "Оплата картой.\n"
        "\n"
        "# Ответственность\n"
        "Стороны несут ответственность.\n"
    )


def test_text_add_without_anchor_goes_to_end_of_section() -> None:
    result = _apply_text([_add("Возможна оплата по счёту.", section="Сроки")])
    assert result == (
        "# Сроки\n"
        "Срок ответа — 10 дней.\n"
        "Оплата картой.\n"
        "\n"
        "Возможна оплата по счёту.\n"
        "\n"
        "# Ответственность\n"
        "Стороны несут ответственность.\n"
    )


def test_text_section_match_is_case_insensitive() -> None:
    result = _apply_text([_add("Дополнение.", section="сроки")])
    assert result.index("Дополнение.") < result.index("# Ответственность")


def test_text_add_falls_back_to_document_end_like_before() -> None:
    result = _apply_text([_add("Новый раздел.", anchor="такого текста нет", section="Нет такого")])
    assert result == MD.rstrip("\n") + "\n\nНовый раздел.\n"


def test_text_anchor_wins_over_section() -> None:
    change = _add("Уточнение.", anchor="Стороны несут ответственность.", section="Сроки")
    result = _apply_text([change])
    assert result.endswith("Стороны несут ответственность.\n\nУточнение.\n")
    assert "Уточнение." not in result.split("# Ответственность")[0]


def test_plain_txt_without_headings_uses_anchor_then_end() -> None:
    txt = "Первая строка.\nВторая строка.\n"
    by_anchor = _apply_text([_add("Вставка.", anchor="Первая строка.")], txt)
    assert by_anchor == "Первая строка.\n\nВставка.\n\nВторая строка.\n"
    at_end = _apply_text([_add("Вставка.", section="Раздел")], txt)
    assert at_end == "Первая строка.\nВторая строка.\n\nВставка.\n"


# ── docx ─────────────────────────────────────────────────────────────────────────────────────────────


def _docx_bytes() -> bytes:
    document = DocxDocument()
    document.add_paragraph("Сроки", style="Heading 1")
    document.add_paragraph("Срок ответа — 10 дней.")
    document.add_paragraph("Оплата картой.")
    document.add_paragraph("Ответственность", style="Heading 1")
    document.add_paragraph("Стороны несут ответственность.")
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _paragraphs(raw: bytes) -> list[str]:
    return [p.text for p in DocxDocument(io.BytesIO(raw)).paragraphs]


def test_docx_add_goes_right_after_anchor_paragraph() -> None:
    raw = DocxExporter().apply_changes(
        _docx_bytes(), [_add("Срок может быть продлён.", anchor="Срок ответа — 10 дней.")]
    )
    assert _paragraphs(raw) == [
        "Сроки",
        "Срок ответа — 10 дней.",
        "Срок может быть продлён.",
        "Оплата картой.",
        "Ответственность",
        "Стороны несут ответственность.",
    ]


def test_docx_add_without_anchor_goes_to_end_of_section() -> None:
    raw = DocxExporter().apply_changes(
        _docx_bytes(), [_add("Возможна оплата по счёту.", section="Сроки")]
    )
    assert _paragraphs(raw) == [
        "Сроки",
        "Срок ответа — 10 дней.",
        "Оплата картой.",
        "Возможна оплата по счёту.",
        "Ответственность",
        "Стороны несут ответственность.",
    ]


def test_docx_add_falls_back_to_document_end() -> None:
    raw = DocxExporter().apply_changes(
        _docx_bytes(), [_add("Новый раздел.", anchor="нет такого", section="Нет такого")]
    )
    assert _paragraphs(raw)[-1] == "Новый раздел."
    assert len(_paragraphs(raw)) == 6
