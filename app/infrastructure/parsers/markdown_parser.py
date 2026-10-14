import re

from app.domain.interfaces.document_parser import DocumentSection, ParsedDocument
from app.domain.text_normalization import normalize_text

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)


class MarkdownParser:
    def parse(self, raw_bytes: bytes) -> ParsedDocument:
        text = normalize_text(raw_bytes.decode("utf-8", errors="replace"))
        matches = list(_HEADING_RE.finditer(text))
        sections: list[DocumentSection] = []
        for i, match in enumerate(matches):
            start = match.start()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            sections.append(
                DocumentSection(ref=match.group(2).strip(), start_offset=start, end_offset=end)
            )
        return ParsedDocument(plain_text=text, sections=sections)
