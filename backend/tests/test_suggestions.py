import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.analysis_service import process_next
from app.config import Settings
from app.main import create_app
from app.model_client import ModelClient, SUGGESTION_REVIEW_PROMPT
from conftest import login


ORIGIN = 'http://testserver'


def test_suggestion_review_prompt_covers_fact_and_strength_boundaries():
    for phrase in (
        '技术栈',
        '数字、年限',
        '参与、协助改成主导、负责',
        '了解改成熟悉、熟练、精通',
        '学习、了解某技术改成实际项目使用',
        '没有结果的原文改写成已经取得结果',
    ):
        assert phrase in SUGGESTION_REVIEW_PROMPT


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


def transport_for(output, suggestion_verdict='supported', suggestion_review_status=200):
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
        elif schema_name == 'resume_suggestion_fact_review':
            if suggestion_review_status != 200:
                return httpx.Response(suggestion_review_status)
            review_input = json.loads(body['messages'][1]['content'])
            assert body['temperature'] == 0
            content = {
                'schema_version': '1',
                'reviews': [{
                    'ordinal': item['ordinal'],
                    'verdict': suggestion_verdict,
                    'issue': (
                        None
                        if suggestion_verdict == 'supported'
                        else '改写存在无法确认的事实。'
                    ),
                } for item in review_input['suggestions']],
            }
        else:
            content = output
        return httpx.Response(200, headers={'x-request-id': 'phase5-test'}, json={
            'choices': [{'message': {'content': json.dumps(content, ensure_ascii=False)}}],
        })
    return httpx.MockTransport(handler)


def create_and_process(client, engine, app, info, output, key='phase5-analysis',
                       resume_text='技能：Python\n项目：参与 FastAPI 开发',
                       job_description='要求 Python\n要求 FastAPI 项目经验',
                       suggestion_verdict='supported',
                       suggestion_review_status=200):
    resume = client.post('/api/v1/resumes', headers=headers(info), json={
        'title': 'Phase 5 简历',
        'original_text': resume_text,
    }).json()
    job = client.post('/api/v1/jobs', headers=headers(info), json={
        'name': '后端工程师', 'description': job_description,
    }).json()
    created = client.post('/api/v1/analyses', headers=headers(info, key), json={
        'resume_id': resume['id'], 'job_id': job['id'],
    }).json()
    assert process_next(engine, app.state.settings, ModelClient(
        app.state.settings,
        transport_for(
            output,
            suggestion_verdict,
            suggestion_review_status,
        ),
    ))
    return resume, created


def faithful_output(original, suggested, *, evidence=None, status='reflected',
                    key='rewrite', job_quote='要求相关项目经验', fact_support=None):
    evidence = original if evidence is None and status == 'reflected' else evidence
    return {
        'schema_version': '1',
        'requirements': [{
            'key': key,
            'job_requirement_quote': job_quote,
            'resume_evidence_quote': evidence,
            'match_status': status,
            'reason': '简历原文体现该要求。' if status == 'reflected' else '简历中未体现该要求。',
            'confirmation_questions': (
                [] if status != 'needs_confirmation'
                else ['请确认是否具备该项经验。']
            ),
        }],
        'suggestions': [{
            'requirement_key': key,
            'source_segment_id': 'p1',
            'original_passage': original,
            'suggested_passage': suggested,
            'rewrite_reason': '精简措辞并突出已有的岗位相关信息。',
            'template_id': 'faithful-rewrite-v1',
            'template_args': {'source_spans': [
                {'start': 0, 'end': len(original)}
            ]},
            'fact_support': fact_support or [{
                'kind': 'other',
                'proposed_fact': suggested,
                'source_quote': original,
            }],
        }],
    }


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


def test_safe_faithful_rewrite_is_actionable_and_accept_is_idempotent(engine):
    original = '使用 Python 和 FastAPI 开发校园活动管理平台后端服务。'
    suggested = '基于 Python 与 FastAPI 开发校园活动管理平台后端服务。'
    app = configured_app(engine)
    with TestClient(app) as client:
        info = login(client).json()
        resume, analysis = create_and_process(
            client,
            engine,
            app,
            info,
            faithful_output(original, suggested),
            'faithful-safe-python',
            original,
            '要求相关项目经验',
        )
        detail = client.get(f"/api/v1/analyses/{analysis['id']}").json()
        suggestion = detail['suggestions'][0]
        assert suggestion['validation_status'] == 'actionable'
        assert suggestion['suggested_text'] == suggested

        draft_url = f"/api/v1/analyses/{analysis['id']}/draft"
        draft = client.get(draft_url).json()
        decision_url = (
            f"/api/v1/analyses/{analysis['id']}/suggestions/"
            f"{suggestion['id']}/decision"
        )
        accepted = client.put(decision_url, headers=headers(info), json={
            'decision': 'accepted',
            'working_markdown': draft['working_markdown'],
            'expected_edit_version': draft['edit_version'],
        })
        assert accepted.status_code == 200
        accepted_draft = accepted.json()['draft']
        assert suggested in accepted_draft['working_markdown']
        assert original not in accepted_draft['working_markdown']

        repeated = client.put(decision_url, headers=headers(info), json={
            'decision': 'accepted',
            'working_markdown': draft['working_markdown'],
            'expected_edit_version': draft['edit_version'],
        })
        assert repeated.status_code == 200
        assert repeated.json()['draft'] == accepted_draft

        stored_resume = client.get(f"/api/v1/resumes/{resume['id']}").json()
        assert stored_resume['original_text'] == original


def test_actionable_faithful_rewrite_uses_safe_backend_reason(engine):
    original = '使用 Python 和 FastAPI 开发后端服务。'
    suggested = '基于 Python 与 FastAPI 开发后端服务。'
    output = faithful_output(original, suggested)
    unsafe_reason = '在某公司主导优化，性能提升 40%。'
    output['suggestions'][0]['rewrite_reason'] = unsafe_reason
    app = configured_app(engine)
    with TestClient(app) as client:
        info = login(client).json()
        _, analysis = create_and_process(
            client,
            engine,
            app,
            info,
            output,
            'faithful-safe-backend-reason',
            original,
            '要求相关项目经验',
        )
        suggestion = client.get(
            f"/api/v1/analyses/{analysis['id']}"
        ).json()['suggestions'][0]

        assert suggestion['validation_status'] == 'actionable'
        assert suggestion['suggested_text'] == suggested
        assert suggestion['rewrite_reason'] == (
            '在不新增简历事实的前提下优化已有表述，'
            '使表达更清晰并更贴合岗位要求。'
        )
        assert unsafe_reason not in suggestion['rewrite_reason']


def test_safe_postgresql_compression_is_actionable(engine):
    original = '使用 PostgreSQL 存储用户信息、活动信息以及报名记录。'
    suggested = '使用 PostgreSQL 管理用户、活动及报名相关数据。'
    app = configured_app(engine)
    with TestClient(app) as client:
        info = login(client).json()
        _, analysis = create_and_process(
            client,
            engine,
            app,
            info,
            faithful_output(original, suggested),
            'faithful-safe-postgresql',
            original,
            '要求相关项目经验',
        )
        suggestion = client.get(
            f"/api/v1/analyses/{analysis['id']}"
        ).json()['suggestions'][0]
        assert suggestion['validation_status'] == 'actionable'
        assert suggestion['suggested_text'] == suggested


@pytest.mark.parametrize(('original', 'suggested', 'expected_code'), [
    (
        '使用 Python 和 FastAPI 开发后端服务。',
        '使用 Python、FastAPI 和 Redis 开发后端服务。',
        'UNSUPPORTED_ASCII_FACT',
    ),
    (
        '优化接口性能。',
        '优化接口性能，响应时间降低 40%。',
        'UNSUPPORTED_NUMBER',
    ),
    (
        '参与 REST API 开发。',
        '独立负责 REST API 设计与开发。',
        'RESPONSIBILITY_INFLATION',
    ),
    (
        '了解 Redis 基本概念。',
        '熟练使用 Redis。',
        'CAPABILITY_INFLATION',
    ),
])
def test_faithful_rewrite_deterministic_fact_inflation_is_blocked(
    engine,
    original,
    suggested,
    expected_code,
):
    app = configured_app(engine)
    with TestClient(app) as client:
        info = login(client).json()
        _, analysis = create_and_process(
            client,
            engine,
            app,
            info,
            faithful_output(original, suggested),
            f'faithful-blocked-{expected_code}',
            original,
            '要求相关项目经验',
        )
        suggestion = client.get(
            f"/api/v1/analyses/{analysis['id']}"
        ).json()['suggestions'][0]
        assert suggestion['validation_status'] == 'blocked'
        assert suggestion['suggested_text'] is None
        assert expected_code in suggestion['blocked_detail_codes']


def test_information_gap_cannot_produce_actionable_faithful_rewrite(engine):
    original = '使用 Python 开发后端服务。'
    output = faithful_output(
        original,
        '使用 Python 和 Kubernetes 开发后端服务。',
        evidence=None,
        status='needs_confirmation',
        key='kubernetes',
        job_quote='要求 Kubernetes',
    )
    app = configured_app(engine)
    with TestClient(app) as client:
        info = login(client).json()
        _, analysis = create_and_process(
            client,
            engine,
            app,
            info,
            output,
            'faithful-kubernetes-gap',
            original,
            '要求 Kubernetes',
        )
        suggestion = client.get(
            f"/api/v1/analyses/{analysis['id']}"
        ).json()['suggestions'][0]
        assert suggestion['validation_status'] == 'blocked'
        assert suggestion['suggested_text'] is None
        assert 'REQUIREMENT_NOT_REFLECTED' in suggestion['blocked_detail_codes']


def test_markdown_list_segment_allows_safe_faithful_rewrite(engine):
    original = '- 使用 Python 和 FastAPI 开发后端服务。'
    suggested = '- 基于 Python 与 FastAPI 开发后端服务。'
    app = configured_app(engine)
    with TestClient(app) as client:
        info = login(client).json()
        _, analysis = create_and_process(
            client,
            engine,
            app,
            info,
            faithful_output(original, suggested),
            'faithful-existing-markdown',
            original,
            '要求相关项目经验',
        )
        suggestion = client.get(
            f"/api/v1/analyses/{analysis['id']}"
        ).json()['suggestions'][0]
        assert suggestion['validation_status'] == 'actionable'
        assert suggestion['suggested_text'] == suggested
        assert 'ALREADY_MARKDOWN_LIST' not in suggestion['blocked_detail_codes']


@pytest.mark.parametrize(('verdict', 'status'), [
    ('uncertain', 200),
    ('supported', 503),
])
def test_faithful_rewrite_review_risk_or_failure_is_fail_closed(
    engine,
    verdict,
    status,
):
    original = '使用 Python 开发后端服务。'
    suggested = '基于 Python 开发后端服务。'
    app = configured_app(engine)
    with TestClient(app) as client:
        info = login(client).json()
        _, analysis = create_and_process(
            client,
            engine,
            app,
            info,
            faithful_output(original, suggested),
            f'faithful-review-{verdict}-{status}',
            original,
            '要求相关项目经验',
            suggestion_verdict=verdict,
            suggestion_review_status=status,
        )
        suggestion = client.get(
            f"/api/v1/analyses/{analysis['id']}"
        ).json()['suggestions'][0]
        assert suggestion['validation_status'] == 'blocked'
        assert suggestion['suggested_text'] is None
