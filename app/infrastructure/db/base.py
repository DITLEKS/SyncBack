"""
Базовый declarative-класс для всех ORM-моделей.
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
