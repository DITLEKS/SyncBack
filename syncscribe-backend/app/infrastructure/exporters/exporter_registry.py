from app.infrastructure.db.models.enums import DocumentFormat
from app.infrastructure.exporters.docx_exporter import DocxExporter
from app.infrastructure.exporters.text_exporter import TextExporter


class DocumentExporterRegistry:
    def __init__(self):
        self._docx_exporter = DocxExporter()
        self._text_exporter = TextExporter()

    def get_exporter(self, document_format: DocumentFormat):
        if document_format in (DocumentFormat.DOCX, DocumentFormat.DOC):
            return self._docx_exporter
        return self._text_exporter
