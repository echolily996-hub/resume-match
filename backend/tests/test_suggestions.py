import json

import httpx
from fastapi.testclient import TestClient

from app.analysis_service import process_next
from app.config import Settings
from app.main import create_app
from app.model_client import ModelClient
from conftest import login


ORIGIN = 'http://testserver'


def configured_app(engine):
    return create_app(Settings(
        database_url=str(engine.url), origins=(ORIGIN,),
        model_base_url='https://model.test/v1', model_api_key='test-api-key',
        model_name='test-model', model_response_mode='json_schema',
        analysis_worker_enabled=False,
    ), engine)


def headers(info, key=None):
    result = {'Origin': ORIGIN, 'X-CSRF-Token': info['csrf_token']}
    if key:
        result['Idempotency-Key'] = key
    return result


def transport_for(output):
    def handler(request):
        body = json.loads(request.content)
        schema_name = body['response_format']['json_schema']['name']
        if schema_name == 'resume_job_fact_review':
            review_input = json.loads(body['messages'][1]['content'])
            content = {
                'schema_version': '1',
                'reviews': [{
                    'key': item['key'], 'match_verdict': 'supported',
                    'reason_verdict': 'supported', 'match_issue': None,
                    'reason_issue': None,
                } for item in review_input['requirements']],
            }
        else:
            content = output
        return httpx.Response(200, headers={'x-request-id': 'phase5-test'}, json={
            'choices': [{'message': {'content': json.dumps(content, ensure_ascii=False)}}],
        })
    return httpx.MockTransport(handler)


def create_and_process(client, engine, app, info, output, key='phase5-analysis'):
    resume = client.post('/api/v1/resumes', headers=headers(info), json={
        'title': 'Phase 5 简历',
        'original_text': '技能：Python\n项目：参与 FastAPI 开发',
    }).json()
    job = client.post('/api/v1/jobs', headers=headers(info), json={
        'name': '后端工程师', 'description': '要求 Python\n要求 FastAPI 项目经验',
    }).json()
    created = client.post('/api/v1/analyses', headers=headers(info, key), json={
        'resume_id': resume['id'], 'job_id': job['id'],
    }).json()
    assert process_next(engine, app.state.settings, ModelClient(
        app.state.settings, transport_for(output)))
    return resume, created


def reflected_output():
    return {'schema_version': '1', 'requirements': [
        {
            'key': 'python', 'job_requirement_quote': '要求 Python',
            'resume_evidence_quote': '技能：Python', 'match_status': 'reflected',
            'reason': '简历原文体现该要求。', 'confirmation_questions': [],
        },
        {
            'key': 'fastapi', 'job_requirement_quote': '要求 FastAPI 项目经验',
            'resume_evidence_quote': '项目：参与 FastAPI 开发', 'match_status': 'reflected',
            'reason': '简历原文体现该要求。', 'confirmation_questions': [],
        },
    ], 'suggestions': [
        {
            'requirement_key': 'python', 'source_segment_id': 'p1',
            'original_passage': '模型改写的原段落',
            'suggested_passage': '- 技能：Java',
            'rewrite_reason': '模型声称应加入 Java。',
            'template_id': 'markdown-bullet-v1',
            'template_args': {'source_spans': [{'start': 0, 'end': 9}]},
            'fact_support': [{
                'kind': 'skill', 'proposed_fact': 'Java',
                'source_quote': 'Python',
            }],
        },
        {
            'requirement_key': 'fastapi', 'source_segment_id': 'p2',
            'original_passage': '项目：参与 FastAPI 开发',
            'suggested_passage': '- 项目：参与 FastAPI 开发',
            'rewrite_reason': '整理为 Markdown 条目，便于阅读。',
            'template_id': 'markdown-bullet-v1',
            'template_args': {'source_spans': [{'start': 10, 'end': 27}]},
            'fact_support': [],
        },
    ]}


def test_suggestion_decisions_update_independent_draft_and_persist(engine):
    app = configured_app(engine)
    with TestClient(app) as client:
        info = login(client).json()
        resume, analysis = create_and_process(
            client, engine, app, info, reflected_output())
        detail = client.get(f"/api/v1/analyses/{analysis['id']}").json()
        assert detail['state'] == 'succeeded'
        assert len(detail['suggestions']) == 2
        assert all(item['decision'] == 'pending' for item in detail['suggestions'])
        assert all(item['validation_status'] == 'actionable'
                   for item in detail['suggestions'])
        assert all(item['suggested_text'].removeprefix('- ') == item['original_text']
                   for item in detail['suggestions'])
        first, second = detail['suggestions']
        assert first['validation_status'] == 'actionable'
        assert first['suggested_text'] == '- 技能：Python'
        assert first['rewrite_reason'] == '仅调整已有原文的展示格式，不新增或改写事实。'
        assert first['blocked_detail_codes'] == [
            'SOURCE_PASSAGE_MISMATCH',
            'RENDERED_TEXT_MISMATCH',
            'FACT_SUPPORT_NOT_LOCAL',
        ]
        assert first['fact_support_failures'] == [{
            'ordinal': 1,
            'codes': ['PROPOSED_FACT_DIFFERS_FROM_SOURCE'],
        }]

        draft_url = f"/api/v1/analyses/{analysis['id']}/draft"
        draft = client.get(draft_url).json()
        assert draft['working_markdown'] == '# 简历正文\n\n技能：Python\n项目：参与 FastAPI 开发'
        assert (draft['edit_version'], draft['saved_version']) == (1, 0)

        decision_url = (f"/api/v1/analyses/{analysis['id']}/suggestions/"
                        f"{first['id']}/decision")
        accepted = client.put(decision_url, headers=headers(info), json={
            'decision': 'accepted', 'working_markdown': draft['working_markdown'],
            'expected_edit_version': draft['edit_version'],
        })
        assert accepted.status_code == 200
        accepted_body = accepted.json()
        assert accepted_body['suggestion']['decision'] == 'accepted'
        assert accepted_body['draft']['working_markdown'].startswith('# 简历正文\n\n- 技能：Python')
        assert 'Java' not in accepted_body['draft']['working_markdown']
        assert '模型改写' not in accepted_body['draft']['working_markdown']
        assert accepted_body['draft']['edit_version'] == 2
        assert accepted_body['draft']['saved_version'] == 0

        repeated = client.put(decision_url, headers=headers(info), json={
            'decision': 'accepted', 'working_markdown': draft['working_markdown'],
            'expected_edit_version': 1,
        })
        assert repeated.status_code == 200
        assert repeated.json()['draft']['edit_version'] == 2

        ignored = client.put(
            f"/api/v1/analyses/{analysis['id']}/suggestions/{second['id']}/decision",
            headers=headers(info), json={'decision': 'ignored'})
        assert ignored.status_code == 200
        assert ignored.json()['suggestion']['decision'] == 'ignored'
        assert ignored.json()['draft'] == repeated.json()['draft']

        original = client.get(f"/api/v1/resumes/{resume['id']}").json()
        assert original['original_text'] == '技能：Python\n项目：参与 FastAPI 开发'
        refreshed = client.get(f"/api/v1/analyses/{analysis['id']}").json()
        assert [item['decision'] for item in refreshed['suggestions']] == [
            'accepted', 'ignored']
        assert refreshed['resume_snapshot'] == original['original_text']

        other = login(client, 'demo_b', 'Demo-B-2026!').json()
        assert client.get(draft_url).status_code == 404
        assert client.put(decision_url, headers=headers(other), json={
            'decision': 'accepted', 'working_markdown': draft['working_markdown'],
            'expected_edit_version': 1,
        }).status_code == 404

        again = login(client).json()
        assert again['user']['username'] == 'demo_a'
        assert client.get(draft_url).json()['edit_version'] == 2
        persisted = client.get(f"/api/v1/analyses/{analysis['id']}").json()
        assert [item['decision'] for item in persisted['suggestions']] == [
            'accepted', 'ignored']


def test_confirmation_gap_creates_no_actionable_suggestion(engine):
    app = configured_app(engine)
    with TestClient(app) as client:
        info = login(client).json()
        output = {'schema_version': '1', 'requirements': [{
            'key': 'kubernetes', 'job_requirement_quote': '要求 Python',
            'resume_evidence_quote': None, 'match_status': 'needs_confirmation',
            'reason': '简历中未提及该项要求。',
            'confirmation_questions': ['请确认是否具备该项经验。'],
        }], 'suggestions': [{
            'requirement_key': 'kubernetes', 'source_segment_id': 'p1',
            'original_passage': '技能：Python ',
            'suggested_passage': '- 技能：Java',
            'rewrite_reason': '模型候选不得进入正文。',
            'template_id': 'markdown-bullet-v1',
            'template_args': {'source_spans': [{'start': 0, 'end': 9}]},
            'fact_support': [{
                'kind': 'skill', 'proposed_fact': 'Java',
                'source_quote': '不存在的原文',
            }],
        }]}
        _, analysis = create_and_process(
            client, engine, app, info, output, 'confirmation-gap')
        detail = client.get(f"/api/v1/analyses/{analysis['id']}").json()
        assert detail['state'] == 'succeeded'
        assert len(detail['suggestions']) == 1
        suggestion = detail['suggestions'][0]
        assert suggestion['validation_status'] == 'blocked'
        assert suggestion['suggested_text'] is None
        assert suggestion['blocked_detail_codes'] == [
            'REQUIREMENT_NOT_REFLECTED',
            'SOURCE_PASSAGE_MISMATCH',
            'RENDERED_TEXT_MISMATCH',
            'FACT_SUPPORT_NOT_LOCAL',
        ]
        assert suggestion['fact_support_failures'] == [{
            'ordinal': 1,
            'codes': [
                'SOURCE_QUOTE_NOT_IN_ORIGINAL',
                'PROPOSED_FACT_DIFFERS_FROM_SOURCE',
            ],
        }]
        draft = client.get(f"/api/v1/analyses/{analysis['id']}/draft").json()
        rejected = client.put(
            f"/api/v1/analyses/{analysis['id']}/suggestions/{suggestion['id']}/decision",
            headers=headers(info), json={
                'decision': 'accepted', 'working_markdown': draft['working_markdown'],
                'expected_edit_version': draft['edit_version'],
            })
        assert rejected.status_code == 409
        assert rejected.json()['error']['code'] == 'SUGGESTION_BLOCKED'
        assert client.get(f"/api/v1/analyses/{analysis['id']}/draft").json() == draft


def test_actionable_requires_evidence_inside_canonical_segment(engine):
    app = configured_app(engine)
    with TestClient(app) as client:
        info = login(client).json()
        output = reflected_output()
        output['suggestions'] = [{
            'requirement_key': 'fastapi', 'source_segment_id': 'p1',
            'original_passage': '技能：Python',
            'suggested_passage': '- 技能：Python',
            'rewrite_reason': '仅调整格式。',
            'template_id': 'markdown-bullet-v1',
            'template_args': {'source_spans': [{'start': 0, 'end': 9}]},
            'fact_support': [],
        }]
        _, analysis = create_and_process(
            client, engine, app, info, output, 'evidence-outside-segment')
        suggestion = client.get(
            f"/api/v1/analyses/{analysis['id']}").json()['suggestions'][0]
        assert suggestion['validation_status'] == 'blocked'
        assert suggestion['suggested_text'] is None


def test_accept_requires_unique_source_location(engine):
    app = configured_app(engine)
    with TestClient(app) as client:
        info = login(client).json()
        _, analysis = create_and_process(
            client, engine, app, info, reflected_output(), 'location-conflict')
        detail = client.get(f"/api/v1/analyses/{analysis['id']}").json()
        suggestion = detail['suggestions'][0]
        version_conflict = client.put(
            f"/api/v1/analyses/{analysis['id']}/suggestions/{suggestion['id']}/decision",
            headers=headers(info), json={
                'decision': 'accepted',
                'working_markdown': '# 简历正文\n\n技能：Python\n项目：参与 FastAPI 开发',
                'expected_edit_version': 2,
            })
        assert version_conflict.status_code == 409
        assert version_conflict.json()['error']['code'] == 'VERSION_CONFLICT'
        result = client.put(
            f"/api/v1/analyses/{analysis['id']}/suggestions/{suggestion['id']}/decision",
            headers=headers(info), json={
                'decision': 'accepted', 'working_markdown': '原文已经被手动移除',
                'expected_edit_version': 1,
            })
        assert result.status_code == 409
        assert result.json()['error']['code'] == 'SUGGESTION_CONFLICT'
        assert client.get(f"/api/v1/analyses/{analysis['id']}").json()[
            'suggestions'][0]['decision'] == 'pending'
        assert client.get(f"/api/v1/analyses/{analysis['id']}/draft").json()[
            'edit_version'] == 1
