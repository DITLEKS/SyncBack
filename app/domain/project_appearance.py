"""Внешний вид карточки проекта: цвет из фиксированной палитры и иконка."""

from __future__ import annotations

from app.domain.exceptions import InvalidProjectColorError

# Hex без '#'. Палитра закрытая, чтобы карточки оставались различимыми и контрастными.
PROJECT_COLORS: tuple[str, ...] = (
    "3B82F6",  # blue
    "8B5CF6",  # violet
    "10B981",  # emerald
    "F59E0B",  # amber
    "EF4444",  # red
    "EC4899",  # pink
    "14B8A6",  # teal
    "F97316",  # orange
)


def normalize_project_color(value: str) -> str:
    """Цвет из палитры в верхнем регистре; иначе InvalidProjectColorError."""
    color = value.strip().lstrip("#").upper()
    if color not in PROJECT_COLORS:
        raise InvalidProjectColorError(
            f"Недопустимый цвет проекта: {value!r}. Допустимые значения: {', '.join(PROJECT_COLORS)}"
        )
    return color


def default_project_color(existing_projects: int) -> str:
    """Цвет нового проекта по кругу палитры: соседние карточки владельца не совпадают по цвету."""
    return PROJECT_COLORS[existing_projects % len(PROJECT_COLORS)]


def normalize_project_icon(value: str) -> str | None:
    """Пустая иконка означает иконку по умолчанию."""
    icon = value.strip()
    return icon or None
