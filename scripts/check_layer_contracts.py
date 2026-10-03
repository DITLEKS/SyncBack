"""Статическая проверка контрактов между слоями.

Находит вызовы методов, которых нет у получателя, и вызовы с аргументами,
не совпадающими с сигнатурой. Получатель определяется по аннотациям типов:
параметры функций (`service: SourceService`), атрибуты классов
(`documents: IDocumentRepository`) и присваивания в `__init__`
(`self._uow = uow`, `self.documents = DocumentRepository(session)`).

Это не замена mypy: проверка узкая и быстрая, зато ловит расхождения
портов и реализаций ещё до того, как они дадут 500 в рантайме.

Запуск: `python scripts/check_layer_contracts.py [путь к пакету]`, код выхода 1 при находках.
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Method:
    params: list[str]
    accepts_anything: bool  # *args или **kwargs


@dataclass
class ClassInfo:
    path: Path
    methods: dict[str, Method] = field(default_factory=dict)
    attrs: dict[str, str] = field(default_factory=dict)  # имя атрибута -> имя класса
    bases: list[str] = field(default_factory=list)


def _annotation_name(node: ast.expr | None) -> str | None:
    """Имя класса из аннотации: `Foo`, `Foo | None`, `"Foo"`, `pkg.Foo`."""
    if node is None:
        return None
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value.split("|")[0].strip()
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return _annotation_name(node.left) or _annotation_name(node.right)
    return None


def _collect_classes(root: Path) -> dict[str, ClassInfo]:
    classes: dict[str, ClassInfo] = {}
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            info = ClassInfo(path=path, bases=[b for b in map(_annotation_name, node.bases) if b])
            for stmt in node.body:
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    name = _annotation_name(stmt.annotation)
                    if name:
                        info.attrs[stmt.target.id] = name
                elif isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef):
                    args = stmt.args
                    params = [a.arg for a in args.posonlyargs + args.args]
                    if not _is_static(stmt):
                        params = params[1:]
                    params += [a.arg for a in args.kwonlyargs]
                    info.methods[stmt.name] = Method(
                        params, args.vararg is not None or args.kwarg is not None
                    )
                    if stmt.name == "__init__":
                        info.attrs.update(_init_attrs(stmt))
            classes[node.name] = info
    return classes


def _is_static(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(isinstance(d, ast.Name) and d.id == "staticmethod" for d in func.decorator_list)


def _init_attrs(init: ast.FunctionDef | ast.AsyncFunctionDef) -> dict[str, str]:
    """`self.x = param` (по аннотации параметра) и `self.x = Class(...)`."""
    param_types = {
        a.arg: _annotation_name(a.annotation)
        for a in init.args.args + init.args.kwonlyargs
        if a.annotation is not None
    }
    attrs: dict[str, str] = {}
    for stmt in ast.walk(init):
        if not isinstance(stmt, ast.Assign) or len(stmt.targets) != 1:
            continue
        target = stmt.targets[0]
        if not (
            isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id == "self"
        ):
            continue
        value = stmt.value
        # `self._x = x or default()` — берём тип параметра
        if isinstance(value, ast.BoolOp):
            value = value.values[0]
        if isinstance(value, ast.Name) and param_types.get(value.id):
            attrs[target.attr] = param_types[value.id]  # type: ignore[assignment]
        elif isinstance(value, ast.Call):
            name = _annotation_name(value.func)
            if name:
                attrs[target.attr] = name
    return attrs


def _resolve_method(classes: dict[str, ClassInfo], class_name: str, method: str) -> Method | None:
    seen: set[str] = set()
    queue = [class_name]
    while queue:
        name = queue.pop(0)
        if name in seen or name not in classes:
            continue
        seen.add(name)
        info = classes[name]
        if method in info.methods:
            return info.methods[method]
        queue.extend(info.bases)
    return None


def _resolve_attr(classes: dict[str, ClassInfo], class_name: str, attr: str) -> str | None:
    seen: set[str] = set()
    queue = [class_name]
    while queue:
        name = queue.pop(0)
        if name in seen or name not in classes:
            continue
        seen.add(name)
        info = classes[name]
        if attr in info.attrs:
            return info.attrs[attr]
        queue.extend(info.bases)
    return None


def _receiver_class(
    node: ast.expr,
    scope: dict[str, str],
    classes: dict[str, ClassInfo],
) -> str | None:
    """Класс получателя для выражения вида `name`, `name.attr`, `self.attr.attr`."""
    if isinstance(node, ast.Name):
        return scope.get(node.id)
    if isinstance(node, ast.Attribute):
        owner = _receiver_class(node.value, scope, classes)
        if owner is None:
            return None
        return _resolve_attr(classes, owner, node.attr)
    return None


def _check_call(
    call: ast.Call,
    scope: dict[str, str],
    classes: dict[str, ClassInfo],
) -> str | None:
    if not isinstance(call.func, ast.Attribute):
        return None
    receiver = call.func.value
    class_name = _receiver_class(receiver, scope, classes)
    if class_name is None or class_name not in classes:
        return None
    method_name = call.func.attr
    receiver_text = ast.unparse(receiver)
    method = _resolve_method(classes, class_name, method_name)
    if method is None:
        # Метод может прийти от базового класса вне пакета (например, Protocol/ABC из stdlib)
        if any(base not in classes for base in classes[class_name].bases):
            return None
        return f"{receiver_text}.{method_name}() — метода нет в {class_name}"
    if method.accepts_anything:
        return None
    unknown = [kw.arg for kw in call.keywords if kw.arg and kw.arg not in method.params]
    if unknown:
        return (
            f"{receiver_text}.{method_name}(): неизвестные аргументы {unknown}; "
            f"сигнатура {method.params}"
        )
    has_star = any(isinstance(a, ast.Starred) for a in call.args)
    if not has_star and len(call.args) > len(method.params):
        return (
            f"{receiver_text}.{method_name}(): {len(call.args)} позиционных, "
            f"сигнатура {method.params}"
        )
    return None


def _function_scope(
    func: ast.FunctionDef | ast.AsyncFunctionDef, owner: str | None
) -> dict[str, str]:
    scope: dict[str, str] = {}
    args = func.args
    for a in args.posonlyargs + args.args + args.kwonlyargs:
        name = _annotation_name(a.annotation)
        if name:
            scope[a.arg] = name
    if owner and (args.args or args.posonlyargs) and not _is_static(func):
        scope[(args.posonlyargs + args.args)[0].arg] = owner
    return scope


def check(root: Path) -> list[str]:
    classes = _collect_classes(root)
    problems: list[str] = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            owner = node.name if isinstance(node, ast.ClassDef) else None
            functions = (
                [n for n in node.body if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)]
                if isinstance(node, ast.ClassDef | ast.Module)
                else []
            )
            for func in functions:
                scope = _function_scope(func, owner)
                for inner in ast.walk(func):
                    if isinstance(inner, ast.Call):
                        message = _check_call(inner, scope, classes)
                        if message:
                            problems.append(f"{path}:{inner.lineno}: {message}")
    return problems


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "app")
    problems = check(root)
    for problem in problems:
        print(problem)
    print(f"Контракты слоёв: {len(problems)} расхождений")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
