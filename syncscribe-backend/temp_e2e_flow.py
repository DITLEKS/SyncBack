from pathlib import Path
import json
import urllib.request
import uuid
import time

Path('/tmp/test.txt').write_text('Hello world from SyncScribe test.')


def json_request(method, url, data=None, token=None):
    headers = {}
    if data:
        headers['Content-Type'] = 'application/json'
    if token:
        headers['Authorization'] = f'Bearer {token}'
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    return json.load(urllib.request.urlopen(req))


def multipart_request(url, fields, token=None):
    boundary = '----WebKitFormBoundary' + uuid.uuid4().hex
    body = []
    for name, value in fields.items():
        if isinstance(value, tuple):
            filename, content, content_type = value
            body.append(f'--{boundary}')
            body.append(f'Content-Disposition: form-data; name="{name}"; filename="{filename}"')
            body.append(f'Content-Type: {content_type}')
            body.append('')
            body.append(content)
        else:
            body.append(f'--{boundary}')
            body.append(f'Content-Disposition: form-data; name="{name}"')
            body.append('')
            body.append(value)
    body.append(f'--{boundary}--')
    body.append('')
    data = '\r\n'.join(body).encode()
    headers = {
        'Content-Type': f'multipart/form-data; boundary={boundary}',
        'Content-Length': str(len(data))
    }
    if token:
        headers['Authorization'] = f'Bearer {token}'
    req = urllib.request.Request(url, data=data, headers=headers, method='POST')
    return json.load(urllib.request.urlopen(req))


token = json_request('POST', 'http://127.0.0.1:8000/api/v1/auth/login', {'email': 'test@example.com', 'password': 'password123'})['access_token']
print('TOKEN', token)

project = json_request('POST', 'http://127.0.0.1:8000/api/v1/projects', {'name': 'Тестовый проект'}, token)
print('PROJECT', project)

doc = multipart_request(
    f'http://127.0.0.1:8000/api/v1/projects/{project["id"]}/documents',
    {'file': ('test.txt', Path('/tmp/test.txt').read_text(), 'text/plain')},
    token
)
print('DOCUMENT', doc)

source = json_request(
    'POST',
    f'http://127.0.0.1:8000/api/v1/projects/{project["id"]}/sources',
    {'name': 'Источник 1', 'type': 'note', 'text_content': 'Актуальный текст из источника истины'},
    token
)
print('SOURCE', source)

attach = json_request(
    'POST',
    f'http://127.0.0.1:8000/api/v1/projects/{project["id"]}/documents/{doc["id"]}/sources',
    {'source_ids': [source['id']]},
    token
)
print('ATTACH', attach)

job = json_request(
    'POST',
    f'http://127.0.0.1:8000/api/v1/projects/{project["id"]}/documents/{doc["id"]}/analysis-jobs',
    None,
    token
)
print('JOB', job)

time.sleep(6)

job_status = json_request(
    'GET',
    f'http://127.0.0.1:8000/api/v1/projects/{project["id"]}/documents/{doc["id"]}/analysis-jobs/{job["id"]}',
    None,
    token
)
print('JOB_STATUS', job_status)

suggestions = json_request(
    'GET',
    f'http://127.0.0.1:8000/api/v1/projects/{project["id"]}/documents/{doc["id"]}/suggestions',
    None,
    token
)
print('SUGGESTIONS', suggestions)

req = urllib.request.Request(
    f'http://127.0.0.1:8000/api/v1/projects/{project["id"]}/documents/{doc["id"]}/export',
    headers={'Authorization': f'Bearer {token}'},
    method='GET'
)
resp = urllib.request.urlopen(req)
data = resp.read()
Path('/tmp/final.txt').write_bytes(data)
print('EXPORT_SAVED', '/tmp/final.txt', 'SIZE', len(data))
print(data[:500].decode('utf-8', 'ignore'))
