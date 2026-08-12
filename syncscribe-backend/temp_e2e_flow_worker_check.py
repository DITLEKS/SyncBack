from pathlib import Path
import json
import urllib.request
import urllib.error
import uuid
import time

Path('test_worker2.txt').write_text('Hello world from SyncScribe worker test.')


def json_request(method, url, data=None, token=None):
    headers = {}
    if data is not None:
        headers['Content-Type'] = 'application/json'
    if token:
        headers['Authorization'] = f'Bearer {token}'
    body = json.dumps(data).encode('utf-8') if data is not None else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        resp = urllib.request.urlopen(req)
        return json.load(resp)
    except urllib.error.HTTPError as e:
        print('ERROR', method, url, 'CODE', e.code)
        try:
            print(e.read().decode('utf-8', errors='replace'))
        except Exception:
            pass
        raise


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
    data = '\r\n'.join(body).encode('utf-8')
    headers = {
        'Content-Type': f'multipart/form-data; boundary={boundary}',
        'Content-Length': str(len(data)),
    }
    if token:
        headers['Authorization'] = f'Bearer {token}'
    req = urllib.request.Request(url, data=data, headers=headers, method='POST')
    try:
        resp = urllib.request.urlopen(req)
        return json.load(resp)
    except urllib.error.HTTPError as e:
        print('ERROR POST', url, 'CODE', e.code)
        try:
            print(e.read().decode('utf-8', errors='replace'))
        except Exception:
            pass
        raise

if __name__ == '__main__':
    base = 'http://127.0.0.1:8000'
    email = f'test+{uuid.uuid4().hex}@example.com'
    print('REGISTER EMAIL', email)
    register = json_request('POST', f'{base}/api/v1/auth/register', {'email': email, 'password': 'password123'})
    print('REGISTER', register)
    login = json_request('POST', f'{base}/api/v1/auth/login', {'email': email, 'password': 'password123'})
    print('LOGIN', login)
    token = login['access_token']

    project = json_request('POST', f'{base}/api/v1/projects', {'name': 'Worker test project'}, token)
    print('PROJECT', project)

    document = multipart_request(
        f'{base}/api/v1/projects/{project["id"]}/documents',
        {'file': ('test_worker2.txt', Path('test_worker2.txt').read_text(), 'text/plain')},
        token,
    )
    print('DOCUMENT', document)

    source = json_request(
        'POST',
        f'{base}/api/v1/projects/{project["id"]}/sources',
        {'name': 'Worker source', 'type': 'note', 'text_content': 'Это тестовый источник для worker.'},
        token,
    )
    print('SOURCE', source)

    attach = json_request(
        'POST',
        f'{base}/api/v1/projects/{project["id"]}/documents/{document["id"]}/sources',
        {'source_ids': [source['id']]},
        token,
    )
    print('ATTACH', attach)

    job = json_request(
        'POST',
        f'{base}/api/v1/projects/{project["id"]}/documents/{document["id"]}/analysis-jobs',
        None,
        token,
    )
    print('JOB', job)
    job_id = job['id']

    for i in range(10):
        time.sleep(2)
        status = json_request(
            'GET',
            f'{base}/api/v1/projects/{project["id"]}/documents/{document["id"]}/analysis-jobs/{job_id}',
            None,
            token,
        )
        print('STATUS', i, status)
        if status['status'] != 'processing':
            break

    suggestions = json_request(
        'GET',
        f'{base}/api/v1/projects/{project["id"]}/documents/{document["id"]}/suggestions',
        None,
        token,
    )
    print('SUGGESTIONS', suggestions)

    req = urllib.request.Request(
        f'{base}/api/v1/projects/{project["id"]}/documents/{document["id"]}/export',
        headers={'Authorization': f'Bearer {token}'},
        method='GET',
    )
    resp = urllib.request.urlopen(req)
    data = resp.read()
    Path('final_worker2.txt').write_bytes(data)
    print('EXPORT_SAVED', 'final_worker2.txt', 'SIZE', len(data))
    print(data[:500].decode('utf-8', 'ignore'))
