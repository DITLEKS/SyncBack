"""Регистрация, вход и проекты через реальные сервисы и репозитории."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from tests.contract.conftest import register_and_login

pytestmark = pytest.mark.asyncio


async def test_register_persists_user_and_login_works(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": "new@example.com", "password": "Correct-Horse-Battery-9"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["email"] == "new@example.com"
    assert body["role"] == "user"

    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "new@example.com", "password": "Correct-Horse-Battery-9"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["access_token"]


async def test_register_duplicate_email_returns_409(client: AsyncClient) -> None:
    payload = {"email": "dup@example.com", "password": "Correct-Horse-Battery-9"}
    assert (await client.post("/api/v1/auth/register", json=payload)).status_code == 201
    assert (await client.post("/api/v1/auth/register", json=payload)).status_code == 409


async def test_login_wrong_password_returns_401(client: AsyncClient) -> None:
    await register_and_login(client, "wrong@example.com")
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "wrong@example.com", "password": "not-the-password-1"},
    )
    assert response.status_code == 401


async def test_create_and_list_project(client: AsyncClient) -> None:
    headers = await register_and_login(client)

    response = await client.post(
        "/api/v1/projects",
        json={"name": "Паспорт продукта", "description": "описание"},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    project = response.json()
    assert project["name"] == "Паспорт продукта"

    response = await client.get("/api/v1/projects", headers=headers)
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert [p["id"] for p in items] == [project["id"]]

    response = await client.get(f"/api/v1/projects/{project['id']}", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["id"] == project["id"]


async def test_foreign_project_is_not_visible(client: AsyncClient) -> None:
    owner = await register_and_login(client, "owner@example.com")
    response = await client.post("/api/v1/projects", json={"name": "Чужой"}, headers=owner)
    project_id = response.json()["id"]

    stranger = await register_and_login(client, "stranger@example.com")
    response = await client.get(f"/api/v1/projects/{project_id}", headers=stranger)
    assert response.status_code in (403, 404)

    response = await client.get(f"/api/v1/projects/{uuid.uuid4()}", headers=stranger)
    assert response.status_code == 404


async def test_projects_require_auth(client: AsyncClient) -> None:
    assert (await client.get("/api/v1/projects")).status_code == 401


async def test_update_and_delete_project(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    response = await client.post("/api/v1/projects", json={"name": "Старое имя"}, headers=headers)
    project_id = response.json()["id"]

    response = await client.patch(
        f"/api/v1/projects/{project_id}", json={"name": "Новое имя"}, headers=headers
    )
    assert response.status_code == 200, response.text
    assert response.json()["name"] == "Новое имя"

    response = await client.get(
        f"/api/v1/projects/{project_id}",
        params=[("include", "sources"), ("include", "documents")],
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["sources"] == []
    assert response.json()["documents"] == []

    response = await client.delete(f"/api/v1/projects/{project_id}", headers=headers)
    assert response.status_code == 204, response.text
    response = await client.get(f"/api/v1/projects/{project_id}", headers=headers)
    assert response.status_code == 404
