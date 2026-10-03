"""Область видимости источника описана одним перечислением — доменным VO."""

from app.domain.value_objects import SourceScopeVO
from app.infrastructure.db.models.source import Source


def test_orm_column_uses_domain_scope_enum() -> None:
    column_type = Source.__table__.c.scope.type
    assert column_type.enum_class is SourceScopeVO
    assert column_type.name == "source_scope"
    assert set(column_type.enums) == {"project", "document"}
    assert Source.__table__.c.scope.server_default.arg == "project"
