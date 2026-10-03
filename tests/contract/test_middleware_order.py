"""CORS-заголовки должны быть на любом ответе, включая отказы middleware."""

from httpx import AsyncClient

from app.core.config import get_settings


async def test_413_from_body_limit_carries_cors_headers(client: AsyncClient) -> None:
    settings = get_settings()
    origin = settings.cors_allowed_origins[0]
    too_big = settings.max_upload_size_bytes + 2 * 1024 * 1024

    response = await client.post(
        "/api/v1/auth/login",
        content=b"",
        headers={"Content-Length": str(too_big), "Origin": origin},
    )

    assert response.status_code == 413
    assert response.headers.get("access-control-allow-origin") == origin
    assert response.headers.get("access-control-allow-credentials") == "true"
    assert response.headers.get("x-request-id")


async def test_preflight_is_answered_before_routers(client: AsyncClient) -> None:
    origin = get_settings().cors_allowed_origins[0]

    response = await client.options(
        "/api/v1/projects",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )

    assert response.status_code == 200, response.text
    assert response.headers.get("access-control-allow-origin") == origin
    assert "POST" in response.headers.get("access-control-allow-methods", "")


async def test_foreign_origin_gets_no_cors_headers(client: AsyncClient) -> None:
    response = await client.get("/health", headers={"Origin": "https://evil.example"})

    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers
