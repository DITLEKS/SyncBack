def build_prompt(document_text: str, source_text: str, document_format: str) -> str:
    return (
        "Ты — ассистент технического писателя. Сравни текущий документ с источником истины "
        "и верни СТРОГО JSON-объект вида {\"suggestions\": [...]}, где каждый элемент массива — "
        "объект с полями section_ref, change_type (add|modify|delete), old_text, new_text. "
        "Не добавляй ничего, кроме этого JSON.\n\n"
        f"Формат документа: {document_format}\n\n"
        f"=== ДОКУМЕНТ ===\n{document_text}\n\n"
        f"=== ИСТОЧНИК ===\n{source_text}\n"
    )
