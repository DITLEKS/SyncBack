"""
Прогон полного сценария 3 раза подряд: login -> analysis-job -> проверка статуса.
Запуск: docker compose exec backend python /srv/app/e2e_test.py

Скрипт сам создаёт проект/документ, если их нет.
"""

import json
import time
import urllib.request
import urllib.error

BASE_URL = "http://127.0.0.1:8000/api/v1"

EMAIL = "test+e2e@example.com"
PASSWORD = "Password123!"

# Leave PROJECT_ID/DOCUMENT_ID empty to let script create them
PROJECT_ID = ""
DOCUMENT_ID = ""


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


def main():
    # Try login, if fails try register
    try:
        login = json_request("POST", f"{BASE_URL}/auth/login", {"email": EMAIL, "password": PASSWORD})
    except urllib.error.HTTPError as exc:
        # try register
        try:
            print("Registering user...")
            _ = json_request("POST", f"{BASE_URL}/auth/register", {"email": EMAIL, "password": PASSWORD})
        except Exception as e:
            print("Registration failed:", e)
        login = json_request("POST", f"{BASE_URL}/auth/login", {"email": EMAIL, "password": PASSWORD})

    token = login.get("access_token")
    if not token:
        print("Failed to obtain token, response:", login)
        return
    print("LOGIN OK")

    # Ensure project
    if not PROJECT_ID:
        projects = json_request("GET", f"{BASE_URL}/projects", None, token)
        if projects:
            PROJECT = projects[0]
            pid = PROJECT["id"]
            print("Using existing project", pid)
        else:
            proj = json_request("POST", f"{BASE_URL}/projects", {"name": "e2e-project"}, token)
            pid = proj["id"]
            print("Created project", pid)
    else:
        pid = PROJECT_ID

    # Ensure document
    if not DOCUMENT_ID:
        docs = json_request("GET", f"{BASE_URL}/projects/{pid}/documents", None, token)
        if docs:
            did = docs[0]["id"]
            print("Using existing document", did)
        else:
            # try to create a document record
            try:
                doc = json_request("POST", f"{BASE_URL}/projects/{pid}/documents", {"name": "e2e-doc.txt", "format": "plain_text"}, token)
                did = doc["id"]
                print("Created document", did)
            except Exception as exc:
                print("Create via JSON failed:", exc, "Trying multipart upload via curl")
                # Fallback: upload a small file via curl (curl exists in container)
                import subprocess, os
                tmp = "/tmp/e2e_doc.txt"
                with open(tmp, "w") as f:
                    f.write("Hello world")
                cmd = [
                    "curl", "-s", "-X", "POST", f"{BASE_URL}/projects/{pid}/documents",
                    "-H", f"Authorization: Bearer {token}", "-F", f"file=@{tmp}"
                ]
                proc = subprocess.run(cmd, capture_output=True, text=True)
                if proc.returncode != 0:
                    print("curl upload failed:", proc.stderr)
                    return
                try:
                    doc = json.loads(proc.stdout)
                    did = doc.get("id")
                    if not did:
                        print("Upload response missing id:", proc.stdout)
                        return
                    print("Uploaded document", did)
                except Exception as e:
                    print("Failed to parse upload response:", e, proc.stdout)
                    return
    else:
        did = DOCUMENT_ID

    results = []
    for i in range(1, 4):
        print(f"\n=== Прогон {i} ===")
        job = json_request("POST", f"{BASE_URL}/projects/{pid}/documents/{did}/analysis-jobs", None, token)
        job_id = job["id"]
        print("JOB CREATED:", job)

        # Wait for job to finish (polling)
        timeout = 300
        interval = 3
        waited = 0
        final_status = None
        while waited < timeout:
            status = json_request("GET", f"{BASE_URL}/projects/{pid}/documents/{did}/analysis-jobs/{job_id}", None, token)
            print("JOB STATUS:", status)
            if status.get("status") in ("success", "failed"):
                final_status = status
                break
            time.sleep(interval)
            waited += interval

        if final_status is None:
            print(f"Job {job_id} did not finish within {timeout}s, last status: {status.get('status')}")
            results.append(status)
        else:
            results.append(final_status)

    print("\n=== ИТОГ ===")
    for i, r in enumerate(results, 1):
        print(f"Прогон {i}: status={r.get('status')}, error_code={r.get('error_code')}, error_message={r.get('error_message')}")


if __name__ == "__main__":
    main()
