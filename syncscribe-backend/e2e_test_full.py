"""
Полный самодостаточный E2E-сценарий: регистрация -> логин -> проект -> документ ->
источник -> привязка источника -> 3 прогона анализа подряд -> suggestions -> export.

Запуск: docker compose exec backend python /srv/app/e2e_test_full.py
"""

import json
import time
import uuid as uuid_module
import urllib.request
from pathlib import Path

BASE_URL = "http://127.0.0.1:8000/api/v1"

# Уникальный email на каждый запуск, чтобы не упираться в "уже зарегистрирован"
EMAIL = f"e2e_{uuid_module.uuid4().hex[:8]}@example.com"
PASSWORD = "password123"


def json_request(method, url, data=None, token=None):
    headers = {}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(req) as resp:
        return json.load(resp)


def multipart_request(url, filename, content, content_type, token=None):
    boundary = "----SyncScribeBoundary" + uuid_module.uuid4().hex
    parts = [
        f"--{boundary}",
        f'Content-Disposition: form-data; name="file"; filename="{filename}"',
        f"Content-Type: {content_type}",
        "",
        content,
        f"--{boundary}--",
        "",
    ]
    data = "\r\n".join(parts).encode()
    headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req) as resp:
        return json.load(resp)


def main():
    print("=== Регистрация ===")
    json_request("POST", f"{BASE_URL}/auth/register", {"email": EMAIL, "password": PASSWORD})

    print("=== Логин ===")
    login = json_request("POST", f"{BASE_URL}/auth/login", {"email": EMAIL, "password": PASSWORD})
    token = login["access_token"]

    print("=== Создание проекта ===")
    project = json_request("POST", f"{BASE_URL}/projects", {"name": "E2E проект"}, token)
    project_id = project["id"]
    print("PROJECT_ID:", project_id)

    print("=== Загрузка документа ===")
    doc = multipart_request(
        f"{BASE_URL}/projects/{project_id}/documents",
        "test.txt",
        "Исходный текст документа, который нужно актуализировать.",
        "text/plain",
        token,
    )
    document_id = doc["id"]
    print("DOCUMENT_ID:", document_id)

    print("=== Создание источника ===")
    source = json_request(
        "POST",
        f"{BASE_URL}/projects/{project_id}/sources",
        {"name": "Источник истины", "type": "note", "text_content": "Актуальный текст из источника истины."},
        token,
    )
    source_id = source["id"]
    print("SOURCE_ID:", source_id)

    print("=== Привязка источника к документу ===")
    attach = json_request(
        "POST",
        f"{BASE_URL}/projects/{project_id}/documents/{document_id}/sources",
        {"source_ids": [source_id]},
        token,
    )
    print("ATTACH:", attach)

    print("\n=== Три прогона анализа подряд ===")
    results = []
    for i in range(1, 4):
        print(f"\n--- Прогон {i} ---")
        job = json_request(
            "POST", f"{BASE_URL}/projects/{project_id}/documents/{document_id}/analysis-jobs", None, token
        )
        job_id = job["id"]
        print("JOB_ID:", job_id)

        for attempt in range(1, 11):
            status = json_request(
                "GET", f"{BASE_URL}/projects/{project_id}/documents/{document_id}/analysis-jobs/{job_id}", None, token
            )
            print("STATUS:", status)
            if status.get("status") in {"success", "failed"}:
                break
            time.sleep(3)
        results.append(status)
        time.sleep(1)

    print("\n=== SUGGESTIONS ===")
    suggestions = json_request(
        "GET", f"{BASE_URL}/projects/{project_id}/documents/{document_id}/suggestions", None, token
    )
    print(json.dumps(suggestions, ensure_ascii=False, indent=2))

    if suggestions:
        print("\n=== BULK ACCEPT ===")
        bulk = json_request(
            "POST", f"{BASE_URL}/projects/{project_id}/documents/{document_id}/suggestions/bulk-accept", None, token
        )
        print(bulk)

    print("\n=== EXPORT ===")
    req = urllib.request.Request(
        f"{BASE_URL}/projects/{project_id}/documents/{document_id}/export",
        headers={"Authorization": f"Bearer {token}"},
        method="GET",
    )
    with urllib.request.urlopen(req) as resp:
        data = resp.read()
    Path("/tmp/final_export.txt").write_bytes(data)
    print("EXPORT_SAVED /tmp/final_export.txt, SIZE", len(data))
    print(data.decode("utf-8", "ignore"))

    print("\n=== ИТОГ ПО ТРЁМ ПРОГОНАМ ===")
    for i, r in enumerate(results, 1):
        print(f"Прогон {i}: status={r.get('status')}, error_code={r.get('error_code')}, error_message={r.get('error_message')}")


if __name__ == "__main__":
    main()
