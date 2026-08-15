"""
Проверяем, что запрос с Content-Length больше допустимого лимита отклоняется middleware
ещё до того, как тело запроса будет прочитано целиком (413 без похода в бизнес-логику).
"""

from types import SimpleNamespace

from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

import app.core.body_size_limit_middleware as body_size_limit_middleware


async def _homepage(request):
    return PlainTextResponse("ok")


def _build_client(monkeypatch, max_upload_size_bytes: int):
    fake_settings = SimpleNamespace(max_upload_size_bytes=max_upload_size_bytes, max_upload_size_mb=max_upload_size_bytes // (1024 * 1024) or 1)
    monkeypatch.setattr(body_size_limit_middleware, "get_settings", lambda: fake_settings)

    app = Starlette(routes=[Route("/", _homepage, methods=["POST"])])
    app.add_middleware(body_size_limit_middleware.BodySizeLimitMiddleware)
    return TestClient(app)


def test_rejects_request_over_limit(monkeypatch):
    client = _build_client(monkeypatch, max_upload_size_bytes=10)
    oversized_payload = b"x" * (10 + 2 * 1024 * 1024)  # заведомо больше лимита + overhead

    response = client.post("/", content=oversized_payload, headers={"Content-Length": str(len(oversized_payload))})

    assert response.status_code == 413


def test_allows_request_within_limit(monkeypatch):
    client = _build_client(monkeypatch, max_upload_size_bytes=10 * 1024 * 1024)

    response = client.post("/", content=b"small body")

    assert response.status_code == 200
