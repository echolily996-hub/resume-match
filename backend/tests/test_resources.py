from fastapi.testclient import TestClient

from conftest import login


ORIGIN = {'Origin': 'http://testserver'}


def mutation_headers(info):
    return {**ORIGIN, 'X-CSRF-Token': info['csrf_token']}


def create_resume(client, info, title='后端工程师简历', body='教育背景\n技能\n项目经历'):
    return client.post('/api/v1/resumes', headers=mutation_headers(info),
                       json={'title': title, 'original_text': body})


def create_job(client, info, name='Python 后端工程师', description='负责 API 和数据库开发'):
    return client.post('/api/v1/jobs', headers=mutation_headers(info),
                       json={'name': name, 'description': description})


def test_resources_require_login_and_mutations_require_csrf(client):
    assert client.get('/api/v1/resumes').status_code == 401
    assert client.get('/api/v1/jobs').status_code == 401
    info = login(client).json()
    assert client.post('/api/v1/resumes', headers=ORIGIN,
                       json={'title': '简历', 'original_text': '正文'}).status_code == 403
    assert client.post('/api/v1/jobs', headers={**mutation_headers(info), 'Origin': 'http://evil.test'},
                       json={'name': '岗位', 'description': '描述'}).status_code == 403


def test_resume_create_list_detail_and_original_preserved(client):
    info = login(client).json()
    original = '  教育背景\n技能：Python\n项目经历  '
    created = create_resume(client, info, '  我的简历  ', original)
    assert created.status_code == 201
    item = created.json()
    assert item['title'] == '我的简历'
    assert item['original_text'] == original

    listing = client.get('/api/v1/resumes').json()
    assert listing['total'] == 1
    assert listing['items'][0]['id'] == item['id']
    assert 'original_text' not in listing['items'][0]
    assert client.get(f"/api/v1/resumes/{item['id']}").json() == item
    assert client.patch(f"/api/v1/resumes/{item['id']}", headers=mutation_headers(info),
                        json={'title': '不得修改'}).status_code == 405


def test_job_create_detail_and_versioned_update(client):
    info = login(client).json()
    created = create_job(client, info)
    assert created.status_code == 201
    item = created.json()
    assert item['version'] == 1

    updated = client.put(f"/api/v1/jobs/{item['id']}", headers=mutation_headers(info), json={
        'name': '高级 Python 后端工程师',
        'description': '  负责 API、数据库与代码评审  ',
        'expected_version': 1,
    })
    assert updated.status_code == 200
    assert updated.json()['version'] == 2
    assert updated.json()['description'] == '  负责 API、数据库与代码评审  '
    assert client.get(f"/api/v1/jobs/{item['id']}").json() == updated.json()

    stale = client.put(f"/api/v1/jobs/{item['id']}", headers=mutation_headers(info), json={
        'name': '过期修改', 'description': '不会写入', 'expected_version': 1,
    })
    assert stale.status_code == 409
    assert stale.json()['error']['code'] == 'VERSION_CONFLICT'
    assert stale.json()['error']['details'] == {'current_version': 2}
    assert client.get(f"/api/v1/jobs/{item['id']}").json()['name'] == '高级 Python 后端工程师'


def test_lists_are_paginated_and_do_not_include_other_types(client):
    info = login(client).json()
    for index in range(3):
        assert create_resume(client, info, f'简历 {index}', f'正文 {index}').status_code == 201
        assert create_job(client, info, f'岗位 {index}', f'描述 {index}').status_code == 201
    resumes = client.get('/api/v1/resumes?limit=2&offset=1').json()
    jobs = client.get('/api/v1/jobs?limit=2&offset=1').json()
    assert (resumes['total'], len(resumes['items']), resumes['limit'], resumes['offset']) == (3, 2, 2, 1)
    assert (jobs['total'], len(jobs['items']), jobs['limit'], jobs['offset']) == (3, 2, 2, 1)


def test_input_validation_rejects_blank_long_and_owner_spoof(client):
    info = login(client).json()
    headers = mutation_headers(info)
    cases = [
        ('/api/v1/resumes', {'title': ' ', 'original_text': '正文'}),
        ('/api/v1/resumes', {'title': '简历', 'original_text': ' '}),
        ('/api/v1/resumes', {'title': 'x' * 201, 'original_text': '正文'}),
        ('/api/v1/jobs', {'name': ' ', 'description': '描述'}),
        ('/api/v1/jobs', {'name': '岗位', 'description': ' '}),
        ('/api/v1/jobs', {'name': '岗位', 'description': '描述', 'owner_id': info['user']['id']}),
    ]
    for path, body in cases:
        result = client.post(path, headers=headers, json=body)
        assert result.status_code == 422
        assert result.json()['error']['code'] == 'INVALID_INPUT'
    assert client.get('/api/v1/resumes').json()['total'] == 0
    assert client.get('/api/v1/jobs').json()['total'] == 0


def test_account_isolation_for_lists_details_and_job_update(app):
    with TestClient(app) as account_a, TestClient(app) as account_b:
        info_a = login(account_a).json()
        info_b = login(account_b, 'demo_b', 'Demo-B-2026!').json()
        resume = create_resume(account_a, info_a).json()
        job = create_job(account_a, info_a).json()

        assert account_b.get('/api/v1/resumes').json()['items'] == []
        assert account_b.get('/api/v1/jobs').json()['items'] == []
        assert account_b.get(f"/api/v1/resumes/{resume['id']}").status_code == 404
        assert account_b.get(f"/api/v1/jobs/{job['id']}").status_code == 404
        changed = account_b.put(f"/api/v1/jobs/{job['id']}", headers=mutation_headers(info_b), json={
            'name': '越权修改', 'description': '越权描述', 'expected_version': 1,
        })
        assert changed.status_code == 404
        assert account_a.get(f"/api/v1/jobs/{job['id']}").json()['name'] == 'Python 后端工程师'
