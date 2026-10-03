"""Скрипт проверки направлений импортов между слоями."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_layer_imports.py"
_spec = importlib.util.spec_from_file_location("check_layer_imports", _SCRIPT)
assert _spec is not None and _spec.loader is not None
check_layer_imports = importlib.util.module_from_spec(_spec)
sys.modules["check_layer_imports"] = check_layer_imports
_spec.loader.exec_module(check_layer_imports)


def _write(root: Path, relative: str, source: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def test_domain_runtime_import_of_infrastructure_is_violation(tmp_path: Path) -> None:
    app = tmp_path / "app"
    _write(app, "domain/services/some_service.py", "from app.infrastructure.db import x\n")
    _write(app, "api/routers.py", "from app.workers.tasks import run\n")
    _write(app, "infrastructure/db.py", "from app.domain import y\n")

    violations, type_checking_only = check_layer_imports.check(app)

    assert len(violations) == 2
    assert any("some_service.py:1" in v and "app.infrastructure.db" in v for v in violations)
    assert any("routers.py:1" in v and "app.workers.tasks" in v for v in violations)
    assert type_checking_only == 0


def test_type_checking_imports_are_counted_not_failed(tmp_path: Path) -> None:
    app = tmp_path / "app"
    _write(
        app,
        "domain/interfaces/repos.py",
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from app.infrastructure.db.models import Document\n"
        "def f():\n"
        "    import app.core.config\n",
    )

    violations, type_checking_only = check_layer_imports.check(app)

    assert type_checking_only == 1
    assert len(violations) == 1
    assert "app.core.config" in violations[0]


def test_real_package_has_no_violations() -> None:
    violations, _ = check_layer_imports.check(Path(__file__).resolve().parents[2] / "app")
    assert violations == []
