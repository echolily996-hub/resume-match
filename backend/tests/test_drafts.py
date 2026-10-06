import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Draft, SavedRevision
from conftest import login
from test_suggestions import (configured_app, create_and_process, headers,
                              reflected_output)


def prepared_analysis(client, engine, app, key='phase6-analysis'):
    info = login(client).json()
    resume, analysis = create_and_process(
        client, engine, app, info, reflected_output(), key=key)
    detail = client.get(f"/api/v1/analyses/{analysis['id']}").json()
    return info, resume, analysis, detail


def test_explicit_save_history_snapshot_idempotency_preview_and_export(engine):
    app = configured_app(engine)
    with TestClient(app) as client:
        info, resume, analysis, detail = prepared_analysis(client, engine, app)
        first, second = detail['suggestions']
        draft_url = f"/api/v1/analyses/{analysis['id']}/draft"
        draft = client.get(draft_url).json()

        accepted = client.put(
            f"/api/v1/analyses/{analysis['id']}/suggestions/{first['id']}/decision",
            headers=headers(info), json={
                'decision': 'accepted',
                'working_markdown': draft['working_markdown'],
                'expected_edit_version': draft['edit_version'],
            }).json()
        client.put(
            f"/api/v1/analyses/{analysis['id']}/suggestions/{second['id']}/decision",
            headers=headers(info), json={'decision': 'ignored'})
        body_v1 = accepted['draft']['working_markdown']

        saved_v1 = client.put(draft_url, headers=headers(info), json={
            'body_markdown': body_v1,
            'expected_edit_version': accepted['draft']['edit_version'],
        })
        assert saved_v1.status_code == 200
        saved_v1_body = saved_v1.json()
        assert saved_v1_body['revision']['version'] == 1
        assert saved_v1_body['draft']['edit_version'] == 3
        assert saved_v1_body['draft']['saved_version'] == 1
        assert saved_v1_body['draft']['saved_markdown'] == body_v1
        assert saved_v1_body['revision']['suggestion_decisions'] == {
            first['id']: 'accepted', second['id']: 'ignored',
        }

        replay = client.put(draft_url, headers=headers(info), json={
            'body_markdown': body_v1,
            'expected_edit_version': 2,
        })
        assert replay.status_code == 200
        assert replay.json()['revision']['id'] == saved_v1_body['revision']['id']
        assert replay.json()['draft']['edit_version'] == 3

        body_v2 = body_v1 + '\n\n手工补充内容'
        saved_v2 = client.put(draft_url, headers=headers(info), json={
            'body_markdown': body_v2,
            'expected_edit_version': 3,
        })
        assert saved_v2.status_code == 200
        assert saved_v2.json()['revision']['version'] == 2
        assert saved_v2.json()['draft']['edit_version'] == 4

        stale = client.put(draft_url, headers=headers(info), json={
            'body_markdown': '冲突内容', 'expected_edit_version': 3,
        })
        assert stale.status_code == 409
        assert stale.json()['error']['code'] == 'VERSION_CONFLICT'
        assert stale.json()['error']['details']['current_version'] == 4

        same_body_new_save = client.put(draft_url, headers=headers(info), json={
            'body_markdown': body_v2,
            'expected_edit_version': 4,
        })
        assert same_body_new_save.status_code == 200
        assert same_body_new_save.json()['revision']['version'] == 3
        assert same_body_new_save.json()['draft']['edit_version'] == 5

        history = client.get(f'{draft_url}/revisions').json()
        assert [item['version'] for item in history['items']] == [3, 2, 1]
        assert history['total'] == 3
        revision_v1 = client.get(f'{draft_url}/revisions/1').json()
        revision_v2 = client.get(f'{draft_url}/revisions/2').json()
        assert revision_v1['body_markdown'] == body_v1
        assert revision_v2['body_markdown'] == body_v2
        assert revision_v1['suggestion_decisions'] == {
            first['id']: 'accepted', second['id']: 'ignored',
        }

        latest_preview = client.get(
            f"/api/v1/analyses/{analysis['id']}/preview").json()
        old_preview = client.get(
            f"/api/v1/analyses/{analysis['id']}/preview?version=1").json()
        assert (latest_preview['version'], latest_preview['body_markdown']) == (3, body_v2)
        assert (old_preview['version'], old_preview['body_markdown']) == (1, body_v1)

        exported = client.get(
            f"/api/v1/analyses/{analysis['id']}/export.md?version=1")
        assert exported.status_code == 200
        assert exported.content == body_v1.encode('utf-8')
        assert exported.headers['content-type'].startswith('text/markdown')
        assert exported.headers['content-disposition'] == 'attachment; filename="resume-v1.md"'
        assert exported.headers['x-saved-version'] == '1'
        assert '模型声称' not in body_v1
        assert '需要补充确认' not in body_v1

        original = client.get(f"/api/v1/resumes/{resume['id']}").json()
        assert original['original_text'] == '技能：Python\n项目：参与 FastAPI 开发'


def test_no_saved_revision_and_cross_account_are_safe(engine):
    app = configured_app(engine)
    with TestClient(app) as client:
        info, _, analysis, _ = prepared_analysis(client, engine, app)
        base = f"/api/v1/analyses/{analysis['id']}"
        for path in (f'{base}/preview', f'{base}/export.md'):
            response = client.get(path)
            assert response.status_code == 409
            assert response.json()['error']['code'] == 'NO_SAVED_REVISION'

        other = login(client, 'demo_b', 'Demo-B-2026!').json()
        assert client.get(f'{base}/draft').status_code == 404
        assert client.get(f'{base}/draft/revisions').status_code == 404
        assert client.get(f'{base}/draft/revisions/1').status_code == 404
        assert client.get(f'{base}/preview').status_code == 404
        assert client.get(f'{base}/export.md').status_code == 404
        assert client.put(f'{base}/draft', headers=headers(other), json={
            'body_markdown': '越权内容', 'expected_edit_version': 1,
        }).status_code == 404
        assert info['user']['username'] == 'demo_a'


def test_save_replay_requires_current_suggestion_snapshot(engine):
    app = configured_app(engine)
    with TestClient(app) as client:
        info, _, analysis, detail = prepared_analysis(client, engine, app)
        draft_url = f"/api/v1/analyses/{analysis['id']}/draft"
        draft = client.get(draft_url).json()
        first_save = client.put(draft_url, headers=headers(info), json={
            'body_markdown': draft['working_markdown'],
            'expected_edit_version': draft['edit_version'],
        })
        assert first_save.status_code == 200

        first_suggestion = detail['suggestions'][0]
        ignored = client.put(
            f"/api/v1/analyses/{analysis['id']}/suggestions/"
            f"{first_suggestion['id']}/decision",
            headers=headers(info), json={'decision': 'ignored'})
        assert ignored.status_code == 200
        assert ignored.json()['draft']['edit_version'] == 2

        not_a_replay = client.put(draft_url, headers=headers(info), json={
            'body_markdown': draft['working_markdown'],
            'expected_edit_version': 1,
        })
        assert not_a_replay.status_code == 409
        assert not_a_replay.json()['error']['code'] == 'VERSION_CONFLICT'
        assert client.get(f'{draft_url}/revisions').json()['total'] == 1


def test_new_analysis_does_not_replace_existing_draft_revisions(engine):
    app = configured_app(engine)
    with TestClient(app) as client:
        info, _, first, _ = prepared_analysis(client, engine, app, 'phase6-first')
        first_draft_url = f"/api/v1/analyses/{first['id']}/draft"
        first_draft = client.get(first_draft_url).json()
        saved = client.put(first_draft_url, headers=headers(info), json={
            'body_markdown': first_draft['working_markdown'],
            'expected_edit_version': first_draft['edit_version'],
        }).json()

        _, _, second, _ = prepared_analysis(client, engine, app, 'phase6-second')
        assert second['id'] != first['id']
        preserved = client.get(f'{first_draft_url}/revisions/1').json()
        assert preserved['id'] == saved['revision']['id']
        assert preserved['body_markdown'] == first_draft['working_markdown']
        second_draft = client.get(f"/api/v1/analyses/{second['id']}/draft").json()
        assert second_draft['saved_version'] == 0
        assert second_draft['saved_markdown'] is None


def test_save_failure_rolls_back_draft_update(engine):
    app = configured_app(engine)
    with TestClient(app, raise_server_exceptions=False) as client:
        info, _, analysis, _ = prepared_analysis(client, engine, app)
        draft_url = f"/api/v1/analyses/{analysis['id']}/draft"
        before = client.get(draft_url).json()
        with Session(engine) as db:
            draft = db.scalar(select(Draft).where(
                Draft.analysis_id == uuid.UUID(analysis['id'])))
            db.add(SavedRevision(
                draft_id=draft.id, version=1, body_markdown='预置冲突',
                suggestion_decisions={},
            ))
            db.commit()

        failed = client.put(draft_url, headers=headers(info), json={
            'body_markdown': '不得部分写入',
            'expected_edit_version': before['edit_version'],
        })
        assert failed.status_code == 503
        assert failed.json()['error']['code'] == 'PERSISTENCE_FAILED'
        after = client.get(draft_url).json()
        assert after['working_markdown'] == before['working_markdown']
        assert after['edit_version'] == before['edit_version']
        assert after['saved_version'] == 0
        assert after['saved_markdown'] is None


def test_restore_revision_updates_only_working_draft(engine):
    app = configured_app(engine)
    with TestClient(app) as client:
        info, resume, analysis, detail = prepared_analysis(client, engine, app)
        base = f"/api/v1/analyses/{analysis['id']}"
        draft_url = f'{base}/draft'
        initial = client.get(draft_url).json()
        body_v1 = initial['working_markdown'] + '\n\n版本一正文'
        saved_v1 = client.put(draft_url, headers=headers(info), json={
            'body_markdown': body_v1,
            'expected_edit_version': initial['edit_version'],
        }).json()

        first_suggestion = detail['suggestions'][0]
        ignored = client.put(
            f"{base}/suggestions/{first_suggestion['id']}/decision",
            headers=headers(info), json={'decision': 'ignored'})
        assert ignored.status_code == 200
        decisions_before_restore = {
            item['id']: item['decision']
            for item in client.get(base).json()['suggestions']
        }

        body_v2 = body_v1 + '\n\n版本二正文'
        saved_v2 = client.put(draft_url, headers=headers(info), json={
            'body_markdown': body_v2,
            'expected_edit_version': saved_v1['draft']['edit_version'],
        }).json()
        assert saved_v2['revision']['version'] == 2
        edit_before_restore = saved_v2['draft']['edit_version']
        history_before = {
            version: client.get(f'{draft_url}/revisions/{version}').json()
            for version in (1, 2)
        }

        restored = client.post(
            f'{draft_url}/revisions/1/restore', headers=headers(info), json={
                'expected_edit_version': edit_before_restore,
            })
        assert restored.status_code == 200
        restored_draft = restored.json()
        assert restored_draft['working_markdown'] == body_v1
        assert restored_draft['edit_version'] == edit_before_restore + 1
        assert restored_draft['saved_version'] == 2
        assert restored_draft['saved_markdown'] == body_v2

        history_after = client.get(f'{draft_url}/revisions').json()
        assert [item['version'] for item in history_after['items']] == [2, 1]
        assert history_after['total'] == 2
        for version in (1, 2):
            assert client.get(f'{draft_url}/revisions/{version}').json() == history_before[version]
        decisions_after_restore = {
            item['id']: item['decision']
            for item in client.get(base).json()['suggestions']
        }
        assert decisions_after_restore == decisions_before_restore

        latest_preview = client.get(f'{base}/preview').json()
        latest_export = client.get(f'{base}/export.md')
        assert (latest_preview['version'], latest_preview['body_markdown']) == (2, body_v2)
        assert latest_export.content == body_v2.encode('utf-8')
        assert latest_export.headers['x-saved-version'] == '2'

        stale = client.post(
            f'{draft_url}/revisions/1/restore', headers=headers(info), json={
                'expected_edit_version': edit_before_restore,
            })
        assert stale.status_code == 409
        assert stale.json()['error']['code'] == 'VERSION_CONFLICT'
        assert stale.json()['error']['details']['current_version'] == edit_before_restore + 1

        missing = client.post(
            f'{draft_url}/revisions/999/restore', headers=headers(info), json={
                'expected_edit_version': restored_draft['edit_version'],
            })
        assert missing.status_code == 404

        other = login(client, 'demo_b', 'Demo-B-2026!').json()
        forbidden = client.post(
            f'{draft_url}/revisions/1/restore', headers=headers(other), json={
                'expected_edit_version': restored_draft['edit_version'],
            })
        assert forbidden.status_code == 404

        info = login(client).json()
        saved_v3 = client.put(draft_url, headers=headers(info), json={
            'body_markdown': restored_draft['working_markdown'],
            'expected_edit_version': restored_draft['edit_version'],
        })
        assert saved_v3.status_code == 200
        assert saved_v3.json()['revision']['version'] == 3
        final_history = client.get(f'{draft_url}/revisions').json()
        assert [item['version'] for item in final_history['items']] == [3, 2, 1]
        assert client.get(f'{draft_url}/revisions/1').json() == history_before[1]
        assert client.get(f'{draft_url}/revisions/2').json() == history_before[2]

        original = client.get(f"/api/v1/resumes/{resume['id']}").json()
        assert original['original_text'] == '技能：Python\n项目：参与 FastAPI 开发'
