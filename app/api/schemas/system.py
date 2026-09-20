"""Схемы для /system/* эндпоинтов."""
from pydantic import BaseModel, Field


class CapabilitiesResponse(BaseModel):
    """Статические возможности бэкенда, которые фронт читает один раз при старте."""

    supported_formats: list[str] = Field(
        description="Расширения файлов, которые принимает парсер (без точки, строчные)"
    )
    unsupported_formats: list[str] = Field(
        description="Расширения, которые известны API, но вернут 400 при загрузке"
    )
    max_file_size_mb: int = Field(
        description="Максимальный размер загружаемого файла, МБ"
    )

    model_config = {"json_schema_extra": {
        "example": {
            "supported_formats": ["pdf", "docx", "txt", "md"],
            "unsupported_formats": ["doc"],
            "max_file_size_mb": 50,
        }
    }}
