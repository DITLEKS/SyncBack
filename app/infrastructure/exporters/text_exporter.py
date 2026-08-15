"""
Применяет принятые правки к txt/markdown простой текстовой заменой. Посимвольный diff
сознательно не делаем на этой итерации.
"""
from app.domain.interfaces.document_exporter import AppliedChange


class TextExporter:
    def apply_changes(self, raw_bytes: bytes, changes: list[AppliedChange]) -> bytes:
        text = raw_bytes.decode("utf-8", errors="replace")
        for change in changes:
            if change.change_type == "modify" and change.old_text:
                text = text.replace(change.old_text, change.new_text or "", 1)
            elif change.change_type == "delete" and change.old_text:
                text = text.replace(change.old_text, "", 1)
            elif change.change_type == "add" and change.new_text:
                text = text.rstrip("\n") + "\n\n" + change.new_text + "\n"
        return text.encode("utf-8")
