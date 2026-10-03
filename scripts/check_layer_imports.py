"""Проверка направлений зависимостей между слоями.

Правила:
  - app.domain не импортирует app.api, app.core, app.infrastructure, app.workers
    во время выполнения. Импорты под `if TYPE_CHECKING:` допускаются как
    временный компромисс (ORM-модели в роли сущностей) и только считаются.
  - app.api и app.core не импортируют app.workers: задачи ставятся через порт
    AnalysisQueue, а не вызовом Celery напрямую.

Запуск: `python scripts/check_layer_imports.py [путь к пакету]`, код выхода 1 при нарушениях.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

FORBIDDEN: dict[str, tuple[str, ...]] = {
    "app.domain": ("app.api", "app.core", "app.infrastructure", "app.workers"),
    "app.api": ("app.workers",),
    "app.core": ("app.workers",),
}


def _module_name(path: Path, root: Path) -> str:
    parts = path.relative_to(root.parent).with_suffix("").parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _is_type_checking_block(node: ast.stmt) -> bool:
    if not isinstance(node, ast.If):
        return False
    test = node.test
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
    )


def _child_statements(node: ast.AST) -> list[ast.stmt]:
    children: list[ast.stmt] = []
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.stmt):
            children.append(child)
    return children


def _imports(tree: ast.Module) -> list[tuple[str, int, bool]]:
    """Все импорты модуля: (имя модуля, строка, под TYPE_CHECKING ли)."""
    found: list[tuple[str, int, bool]] = []

    def visit(nodes: list[ast.stmt], type_checking: bool) -> None:
        for node in nodes:
            if isinstance(node, ast.Import):
                found.extend((alias.name, node.lineno, type_checking) for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                found.append((node.module, node.lineno, type_checking))
            elif isinstance(node, ast.If) and _is_type_checking_block(node):
                visit(node.body, True)
                visit(node.orelse, type_checking)
            else:
                visit(_child_statements(node), type_checking)

    visit(tree.body, False)
    return found


def _matches(module: str, prefix: str) -> bool:
    return module == prefix or module.startswith(prefix + ".")


def check(root: Path) -> tuple[list[str], int]:
    violations: list[str] = []
    type_checking_only = 0
    for path in sorted(root.rglob("*.py")):
        module = _module_name(path, root)
        rules = [targets for layer, targets in FORBIDDEN.items() if _matches(module, layer)]
        if not rules:
            continue
        forbidden = tuple(t for targets in rules for t in targets)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for imported, lineno, under_type_checking in _imports(tree):
            if not any(_matches(imported, prefix) for prefix in forbidden):
                continue
            if under_type_checking:
                type_checking_only += 1
                continue
            violations.append(f"{path}:{lineno}: {module} импортирует {imported}")
    return violations, type_checking_only


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "app").resolve()
    violations, type_checking_only = check(root)
    for line in violations:
        print(line)
    print(
        f"Импорты между слоями: {len(violations)} нарушений, "
        f"{type_checking_only} импортов ORM-моделей под TYPE_CHECKING (допустимый компромисс)"
    )
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
