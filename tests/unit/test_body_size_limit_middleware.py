"""Middleware отклоняет тело сверх лимита: по заголовку сразу, без заголовка — по мере чтения."""

from types import SimpleNamespace

from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

import app.core.body_size_limit_middleware as body_size_limit_middleware


async def _homepage(request):
    return PlainTextResponse("ok")


async def _echo_length(request):
    return PlainTextResponse(str(len(await request.body())))


def _build_client(monkeypatch, max_upload_size_bytes: int):
    fake_settings = SimpleNamespace(
        max_upload_size_bytes=max_upload_size_bytes,
        max_upload_size_mb=max_upload_size_bytes // (1024 * 1024) or 1,
    )
    monkeypatch.setattr(body_size_limit_middleware, "get_settings", lambda: fake_settings)

    app = Starlette(
        routes=[
            Route("/", _homepage, methods=["POST"]),
            Route("/echo-length", _echo_length, methods=["POST"]),
        ]
    )
    app.add_middleware(body_size_limit_middleware.BodySizeLimitMiddleware)
    return TestClient(app)


def test_rejects_request_over_limit(monkeypatch):
    client = _build_client(monkeypatch, max_upload_size_bytes=10)
    oversized_payload = b"x" * (10 + 2 * 1024 * 1024)  # заведомо больше лимита + overhead

    response = client.post(
        "/", content=oversized_payload, headers={"Content-Length": str(len(oversized_payload))}
    )

    assert response.status_code == 413


def test_allows_request_within_limit(monkeypatch):
    client = _build_client(monkeypatch, max_upload_size_bytes=10 * 1024 * 1024)

    response = client.post("/", content=b"small body")

    assert response.status_code == 200


def _chunks(total: int, chunk: int = 64 * 1024):
    sent = 0
    while sent < total:
        size = min(chunk, total - sent)
        sent += size
        yield b"x" * size


def test_rejects_chunked_body_over_limit_while_reading(monkeypatch):
    client = _build_client(monkeypatch, max_upload_size_bytes=10)
    response = client.post("/echo-length", content=_chunks(3 * 1024 * 1024))
    assert response.status_code == 413


def test_rejects_body_longer_than_declared_content_length(monkeypatch):
    client = _build_client(monkeypatch, max_upload_size_bytes=10)
    body = b"x" * (3 * 1024 * 1024)
    # Заголовок занижен: решение принимается по фактически прочитанному объёму.
    response = client.post(
        "/echo-length", content=_chunks(len(body)), headers={"Content-Length": "5"}
    )
    assert response.status_code == 413


def test_fastapi_upload_over_limit_is_413_not_400(monkeypatch):
    from fastapi import FastAPI, File, UploadFile

    fake_settings = SimpleNamespace(max_upload_size_bytes=10, max_upload_size_mb=1)
    monkeypatch.setattr(body_size_limit_middleware, "get_settings", lambda: fake_settings)
    calls: list[str] = []
    api = FastAPI()

    @api.post("/upload")
    async def upload(file: UploadFile = File(...)) -> dict[str, int]:
        calls.append(file.filename or "")
        return {"size": file.size or 0}

    api.add_middleware(body_size_limit_middleware.BodySizeLimitMiddleware)
    client = TestClient(api)

    def multipart():
        yield b'--b\r\nContent-Disposition: form-data; name="file"; filename="a.txt"\r\n\r\n'
        yield from _chunks(3 * 1024 * 1024)
        yield b"\r\n--b--\r\n"

    response = client.post(
        "/upload", content=multipart(), headers={"Content-Type": "multipart/form-data; boundary=b"}
    )
    assert response.status_code == 413, response.text
    assert calls == []
