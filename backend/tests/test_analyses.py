import json

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.analysis_service import (ClaimedAnalysis, EvidenceError, claim_next,
                                  fact_review_advisories, finish_failure,
                                  process_next, validated_rows)
from app.config import Settings
from app.main import create_app
from app.model_client import (AnalysisOutput, FACT_REVIEW_PROMPT, FactReviewOutput,
                              ModelClient, RequirementOutput, SYSTEM_PROMPT,
                              validation_error_metadata)
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


def inputs(client, info, job_description='要求 Python，并负责 API 开发'):
    resume = client.post('/api/v1/resumes', headers=headers(info), json={
        'title': '测试简历', 'original_text': '技能：Python\n项目：参与 API 开发',
    }).json()
    job = client.post('/api/v1/jobs', headers=headers(info), json={
        'name': '后端工程师', 'description': job_description,
    }).json()
    return resume, job


def create_analysis(client, info, resume, job, key='analysis-1'):
    return client.post('/api/v1/analyses', headers=headers(info, key), json={
        'resume_id': resume['id'], 'job_id': job['id'],
    })


def valid_output(job_quote='要求 Python', evidence='技能：Python'):
    return {
        'schema_version': '1',
        'requirements': [{
            'key': 'r1', 'job_requirement_quote': job_quote,
            'resume_evidence_quote': evidence, 'match_status': 'reflected',
            'reason': '简历原文体现了该要求。', 'confirmation_questions': [],
        }], 'suggestions': [],
    }


def parse_output(value):
    return AnalysisOutput.model_validate({
        **value, 'suggestions': value.get('suggestions', [])})


def suggestion_candidate(requirement_key='r1', source_segment_id='p1'):
    return {
        'requirement_key': requirement_key,
        'source_segment_id': source_segment_id,
        'original_passage': '技能：Python',
        'suggested_passage': '- 技能：Python',
        'rewrite_reason': '仅调整展示格式。',
        'template_id': 'markdown-bullet-v1',
        'template_args': {'source_spans': [{'start': 0, 'end': 9}]},
        'fact_support': [],
    }


@pytest.mark.parametrize(('output', 'expected_code'), [
    ({
        **valid_output(),
        'requirements': valid_output()['requirements'] * 2,
    }, 'DUPLICATE_REQUIREMENT_KEYS'),
    
    ({
        **valid_output(),
        'suggestions': [suggestion_candidate('unknown')],
    }, 'UNKNOWN_SUGGESTION_REQUIREMENT_KEY'),
    ({'schema_version': '1'}, 'OTHER_MODEL_VALIDATION'),
])
def test_analysis_root_validation_rule_diagnostic(output, expected_code):
    with pytest.raises(ValidationError) as caught:
        AnalysisOutput.model_validate(output)
    metadata = validation_error_metadata(caught.value, json.dumps(output))
    assert metadata['validation_rule_code'] == expected_code
   

def test_analysis_allows_duplicate_suggestion_source_segments():
    output = {
        **valid_output(),
        'requirements': [
            valid_output()['requirements'][0],
            {**valid_output()['requirements'][0], 'key': 'r2'},
        ],
        'suggestions': [
            suggestion_candidate('r1'),
            suggestion_candidate('r2'),
        ],
    }

    parsed = AnalysisOutput.model_validate(output)

    assert len(parsed.suggestions) == 2
    assert (
        parsed.suggestions[0].source_segment_id
        == parsed.suggestions[1].source_segment_id
    )


def test_quote_prompt_and_schema_require_exact_single_paragraph_substrings():
    required_check = '返回的 quote 必须能直接作为输入文本的连续子串查找到；否则不要返回该 quote。'
    assert required_check in SYSTEM_PROMPT
    assert 'resume_evidence_quote 必须为 null' in SYSTEM_PROMPT
    assert '此时不得使用 reflected' in SYSTEM_PROMPT
    assert '增加原文不存在的空格、换行、标点或其他字符' in SYSTEM_PROMPT

    properties = RequirementOutput.model_json_schema()['properties']
    job_description = properties['job_requirement_quote']['description']
    resume_description = properties['resume_evidence_quote']['description']
    for description in (job_description, resume_description):
        assert 'single-paragraph continuous verbatim substring' in description
        assert 'directly findable' in description
        assert 'add punctuation' in description
    assert 'or null when no exact supporting substring is certain' in resume_description
    output_schema = AnalysisOutput.model_json_schema()
    assert 'suggestions' in output_schema['required']
    assert 'template_id=markdown-bullet-v1' in SYSTEM_PROMPT
    assert 'needs_confirmation、not_reflected 和补充问题不得形成建议正文' in SYSTEM_PROMPT


def transport_for(output=None, status=200, match_review_verdict='supported',
                  reason_review_verdict='supported', review_status=200,
                  raw_content=None, finish_reason=None):
    def handler(request):
        assert request.url == 'https://model.test/v1/chat/completions'
        assert request.headers['authorization'] == 'Bearer test-api-key'
        body = json.loads(request.content)
        assert body['model'] == 'test-model'
        assert body['response_format']['type'] == 'json_schema'
        schema_name = body['response_format'].get('json_schema', {}).get('name')
        if schema_name == 'resume_job_fact_review':
            assert body['temperature'] == 0
            if review_status != 200:
                return httpx.Response(review_status, json={'error': {'message': 'review failed'}})
        if status != 200:
            return httpx.Response(status, json={'error': {'message': 'must not leak'}})
        if schema_name == 'resume_job_fact_review':
            review_input = json.loads(body['messages'][1]['content'])
            content = {
                'schema_version': '1',
                'reviews': [{
                    'key': item['key'],
                    'match_verdict': match_review_verdict,
                    'reason_verdict': reason_review_verdict,
                    'match_issue': (None if match_review_verdict == 'supported'
                                    else '证据不足以支持匹配判断'),
                    'reason_issue': (None if reason_review_verdict == 'supported'
                                     else '原因包含证据未支持的附加描述'),
                } for item in review_input['requirements']],
            }
        else:
            content = output or valid_output()
            content = {**content, 'suggestions': content.get('suggestions', [])}
        response_content = (raw_content if raw_content is not None else
                            json.dumps(content, ensure_ascii=False))
        return httpx.Response(200, headers={'x-request-id': 'provider-request-1'}, json={
            'id': 'completion-1',
            'choices': [{'finish_reason': finish_reason,
                         'message': {'content': response_content}}],
        })
    return httpx.MockTransport(handler)


def test_analysis_requires_model_configuration(client):
    info = login(client).json()
    resume, job = inputs(client, info)
    result = create_analysis(client, info, resume, job)
    assert result.status_code == 503
    assert result.json()['error']['code'] == 'MODEL_NOT_CONFIGURED'
    assert client.get('/api/v1/analyses').json()['total'] == 0


def test_create_process_and_view_structured_analysis(engine):
    app = configured_app(engine)
    with TestClient(app) as client:
        info = login(client).json()
        resume, job = inputs(client, info)
        created = create_analysis(client, info, resume, job)
        assert created.status_code == 202
        assert created.json()['state'] == 'queued'

        assert process_next(engine, app.state.settings, ModelClient(
            app.state.settings, transport_for()))
        detail = client.get(f"/api/v1/analyses/{created.json()['id']}").json()
        assert detail['state'] == 'succeeded'
        assert detail['resume_snapshot'] == '技能：Python\n项目：参与 API 开发'
        assert detail['job_description_snapshot'] == '要求 Python，并负责 API 开发'
        assert detail['model_metadata'] == {
            'model': 'test-model', 'attempt_no': 1,
            'duration_ms': detail['model_metadata']['duration_ms'],
            'response_status': 200, 'provider_request_id': 'provider-request-1',
            'fact_review': {
                'model': 'test-model', 'attempt_no': 1,
                'duration_ms': detail['model_metadata']['fact_review']['duration_ms'],
                'response_status': 200, 'provider_request_id': 'provider-request-1',
                'status': 'advisory', 'findings': [],
            },
        }
        requirement = detail['requirements'][0]
        assert requirement['job_requirement_quote'] == '要求 Python'
        assert requirement['job_span'] == {'start': 0, 'end': 9}
        assert requirement['resume_evidence_quote'] == '技能：Python'
        assert requirement['resume_span'] == {'start': 0, 'end': 9}
        assert requirement['match_status'] == 'reflected'
        assert requirement['reason'] == '简历原文体现了该要求。'


def test_running_state_and_failure_retry(engine):
    app = configured_app(engine)
    with TestClient(app) as client:
        info = login(client).json()
        resume, job = inputs(client, info)
        created = create_analysis(client, info, resume, job).json()
        claimed = claim_next(engine)
        assert client.get(f"/api/v1/analyses/{created['id']}").json()['state'] == 'running'
        finish_failure(engine, claimed, 'MODEL_TIMEOUT', '模型分析超时。', {
            'model': 'test-model', 'attempt_no': 1, 'response_status': None,
        })
        failed = client.get(f"/api/v1/analyses/{created['id']}").json()
        assert failed['state'] == 'failed'
        assert failed['error_code'] == 'MODEL_TIMEOUT'

        retried = client.post(f"/api/v1/analyses/{created['id']}/retry",
                              headers=headers(info), json={'expected_attempt_no': 1})
        assert retried.status_code == 202
        assert (retried.json()['state'], retried.json()['attempt_no']) == ('queued', 2)
        assert process_next(engine, app.state.settings, ModelClient(
            app.state.settings, transport_for()))
        completed = client.get(f"/api/v1/analyses/{created['id']}").json()
        assert completed['state'] == 'succeeded'
        assert completed['attempt_no'] == 2
        assert completed['error_code'] is None


def test_service_and_evidence_failures_are_persisted(engine):
    app = configured_app(engine)
    with TestClient(app) as client:
        info = login(client).json()
        resume, job = inputs(client, info)
        service_task = create_analysis(client, info, resume, job, 'service-failure').json()
        process_next(engine, app.state.settings, ModelClient(
            app.state.settings, transport_for(status=500)))
        service_detail = client.get(f"/api/v1/analyses/{service_task['id']}").json()
        assert service_detail['state'] == 'failed'
        assert service_detail['error_code'] == 'MODEL_SERVICE_FAILED'
        assert 'must not leak' not in service_detail['error_message']
        assert service_detail['model_metadata']['response_status'] == 500

        structure_task = create_analysis(client, info, resume, job, 'bad-structure').json()
        process_next(engine, app.state.settings, ModelClient(
            app.state.settings, transport_for({'schema_version': '1', 'requirements': []})))
        structure_detail = client.get(f"/api/v1/analyses/{structure_task['id']}").json()
        assert structure_detail['state'] == 'failed'
        assert structure_detail['error_code'] == 'MODEL_INVALID_OUTPUT'

        suggestion_task = create_analysis(
            client, info, resume, job, 'bad-suggestion-structure').json()
        invalid_suggestion = valid_output()
        invalid_suggestion['suggestions'] = [{
            'requirement_key': 'r1', 'source_segment_id': 'p1',
            'original_passage': '技能：Python',
            'suggested_passage': '- 技能：Python',
            'rewrite_reason': '整理为 Markdown 条目。',
            'template_id': 'markdown-bullet-v1',
            'fact_support': [],
        }]
        process_next(engine, app.state.settings, ModelClient(
            app.state.settings, transport_for(invalid_suggestion)))
        suggestion_detail = client.get(
            f"/api/v1/analyses/{suggestion_task['id']}").json()
        assert suggestion_detail['state'] == 'failed'
        assert suggestion_detail['error_code'] == 'MODEL_INVALID_OUTPUT'
        diagnostic = suggestion_detail['model_metadata']
        assert diagnostic['validation_error_type'] == 'missing'
        assert diagnostic['validation_error_loc'] == 'suggestions.0.template_args'
        assert diagnostic['validation_error_count'] == 1
        assert diagnostic['validation_stage'] == 'MODEL_VALIDATION'
        assert 'json_error_line' not in diagnostic
        assert 'json_error_column' not in diagnostic
        assert 'json_error_position' not in diagnostic
        assert 'original_passage' not in diagnostic
        assert 'suggested_passage' not in diagnostic

        malformed_task = create_analysis(
            client, info, resume, job, 'malformed-json').json()
        malformed = '{"schema_version":"1","requirements":['
        process_next(engine, app.state.settings, ModelClient(
            app.state.settings, transport_for(
                raw_content=malformed, finish_reason='length')))
        malformed_detail = client.get(
            f"/api/v1/analyses/{malformed_task['id']}").json()
        assert malformed_detail['state'] == 'failed'
        assert malformed_detail['error_code'] == 'MODEL_INVALID_OUTPUT'
        malformed_metadata = malformed_detail['model_metadata']
        assert malformed_metadata['provider_finish_reason'] == 'length'
        assert malformed_metadata['response_content_length'] == len(malformed)
        assert malformed_metadata['response_starts_with_object'] is True
        assert malformed_metadata['response_ends_with_object'] is False
        assert malformed_metadata['validation_error_detail'] == 'EOF'
        assert malformed_metadata['validation_stage'] == 'JSON_DECODE'
        with pytest.raises(json.JSONDecodeError) as parse_error:
            json.loads(malformed)
        assert malformed_metadata['json_error_line'] == parse_error.value.lineno
        assert malformed_metadata['json_error_column'] == parse_error.value.colno
        assert malformed_metadata['json_error_position'] == parse_error.value.pos
        assert 'response_content' not in malformed_metadata

        evidence_task = create_analysis(client, info, resume, job, 'bad-evidence').json()
        invalid = valid_output(job_quote='岗位中不存在的要求')
        process_next(engine, app.state.settings, ModelClient(
            app.state.settings, transport_for(invalid)))
        evidence_detail = client.get(f"/api/v1/analyses/{evidence_task['id']}").json()
        assert evidence_detail['state'] == 'failed'
        assert evidence_detail['error_code'] == 'MODEL_INVALID_EVIDENCE'
        assert evidence_detail['model_metadata']['response_status'] == 200
        assert evidence_detail['model_metadata']['model'] == 'test-model'
        assert evidence_detail['model_metadata']['provider_request_id'] == 'provider-request-1'
        assert evidence_detail['model_metadata']['violation_code'] == 'INVALID_JOB_QUOTE'
        assert evidence_detail['model_metadata']['requirement_key'] == 'r1'
        assert evidence_detail['model_metadata']['ordinal'] == 1
        assert evidence_detail['model_metadata']['violation_detail_code'] == 'QUOTE_NOT_FOUND'
        assert evidence_detail['model_metadata']['resume_evidence_present'] is True
        assert evidence_detail['model_metadata']['match_status'] == 'reflected'
        assert 'reason_clause_ordinal' not in evidence_detail['model_metadata']
        assert evidence_detail['requirements'] == []

        reason_task = create_analysis(client, info, resume, job, 'bad-reason-fact').json()
        invalid_reason = valid_output()
        invalid_reason['requirements'][0]['reason'] = '候选人具备机器学习项目经验。'
        process_next(engine, app.state.settings, ModelClient(
            app.state.settings, transport_for(invalid_reason)))
        reason_detail = client.get(f"/api/v1/analyses/{reason_task['id']}").json()
        assert reason_detail['state'] == 'succeeded'
        reason_warning = reason_detail['warnings'][0]
        assert reason_warning['violation_detail_code'] == 'UNSUPPORTED_CONCRETE_FACT'
        assert reason_warning['resume_evidence_present'] is True
        assert reason_warning['match_status'] == 'reflected'
        assert reason_warning['reason_clause_ordinal'] == 1
        assert reason_warning['absence_context_detected'] is False
        assert reason_warning['job_context_detected'] is False
        assert reason_warning['fact_present_in_evidence'] is False
        assert reason_warning['fact_present_in_job_quote'] is False
        assert reason_warning['trigger_token'] == '机器学习'
        assert reason_detail['requirements'][0]['match_status'] == 'needs_confirmation'
        assert '机器学习' not in reason_detail['requirements'][0]['reason']

        ascii_task = create_analysis(client, info, resume, job, 'bad-ascii-fact').json()
        invalid_ascii = valid_output()
        invalid_ascii['requirements'][0].update({
            'key': 'prepare_monthly_annual_financial_kpi_reports',
            'match_status': 'needs_confirmation',
            'reason': '简历未明确说明是否熟悉 Django。',
            'confirmation_questions': ['是否熟悉 Django？'],
        })
        process_next(engine, app.state.settings, ModelClient(
            app.state.settings, transport_for(invalid_ascii)))
        ascii_detail = client.get(f"/api/v1/analyses/{ascii_task['id']}").json()
        assert ascii_detail['state'] == 'succeeded'
        ascii_warning = ascii_detail['warnings'][0]
        assert ascii_warning['violation_detail_code'] == 'UNSUPPORTED_ASCII_FACT'
        assert ascii_warning['absence_context_detected'] is True
        assert ascii_warning['job_context_detected'] is False
        assert ascii_warning['fact_present_in_evidence'] is False
        assert ascii_warning['fact_present_in_job_quote'] is False
        assert ascii_warning['trigger_token'] == 'Django'
        assert ascii_detail['requirements'][0]['match_status'] == 'needs_confirmation'

        affirmative_task = create_analysis(
            client, info, resume, job, 'no-evidence-affirmative').json()
        invalid_affirmative = valid_output(evidence=None)
        invalid_affirmative['requirements'][0].update({
            'key': '在团队环境中独立和协作工作的能力',
            'match_status': 'not_reflected',
            'reason': '候选人具备独立和协作工作的能力。',
        })
        process_next(engine, app.state.settings, ModelClient(
            app.state.settings, transport_for(invalid_affirmative)))
        affirmative_detail = client.get(
            f"/api/v1/analyses/{affirmative_task['id']}").json()
        assert affirmative_detail['state'] == 'succeeded'
        affirmative_warning = affirmative_detail['warnings'][0]
        assert affirmative_warning['violation_detail_code'] == 'NO_EVIDENCE_AFFIRMATIVE'
        assert affirmative_warning['absence_context_detected'] is False
        assert affirmative_warning['job_context_detected'] is False
        assert affirmative_warning['fact_present_in_evidence'] is False
        assert affirmative_warning['fact_present_in_job_quote'] is False
        assert affirmative_warning['trigger_token'] == '独立'
        assert affirmative_detail['requirements'][0]['match_status'] == 'needs_confirmation'

        numeric_task = create_analysis(client, info, resume, job, 'unsupported-number').json()
        invalid_number = valid_output()
        invalid_number['requirements'][0]['reason'] = '候选人将效率提升 50%。'
        process_next(engine, app.state.settings, ModelClient(
            app.state.settings, transport_for(invalid_number)))
        numeric_detail = client.get(f"/api/v1/analyses/{numeric_task['id']}").json()
        assert numeric_detail['state'] == 'failed'
        assert numeric_detail['error_code'] == 'MODEL_INVALID_EVIDENCE'
        assert numeric_detail['model_metadata']['violation_detail_code'] == (
            'UNSUPPORTED_NUMBER')
        assert numeric_detail['requirements'] == []


def test_fact_boundary_hard_validation():
    claimed = ClaimedAnalysis(
        id='analysis-id', attempt_no=1,
        resume_snapshot=(
            '技能：Python\n项目：参与 API 开发，没有量化成果\n参与年度预算编制'),
        job_name_snapshot='后端工程师',
        job_description_snapshot=(
            '要求 Python，并负责 API 开发；按时完成工作或满足截止日期；'
            '注重细节并保持条理；负责年度预算编制'),
    )

    legal = parse_output(valid_output())
    assert len(validated_rows(claimed, legal)) == 1

    missing_deadline = parse_output({
        'schema_version': '1', 'requirements': [{
            'key': 'meet_deadlines',
            'job_requirement_quote': '按时完成工作或满足截止日期',
            'resume_evidence_quote': None, 'match_status': 'not_reflected',
            'reason': '简历中未提及按时完成工作或满足截止日期的能力',
            'confirmation_questions': [],
        }],
    })
    assert len(validated_rows(claimed, missing_deadline)) == 1

    missing_detail = parse_output({
        'schema_version': '1', 'requirements': [{
            'key': 'detail_oriented_organized',
            'job_requirement_quote': '注重细节并保持条理',
            'resume_evidence_quote': None, 'match_status': 'not_reflected',
            'reason': '简历中未体现对细节的高度关注或条理性',
            'confirmation_questions': [],
        }],
    })
    assert len(validated_rows(claimed, missing_detail)) == 1

    forbidden_capability = parse_output({
        'schema_version': '1', 'requirements': [{
            'key': 'detail_oriented_organized',
            'job_requirement_quote': '注重细节并保持条理',
            'resume_evidence_quote': None, 'match_status': 'not_reflected',
            'reason': '求职者没有这些能力。', 'confirmation_questions': [],
        }],
    })
    with pytest.raises(EvidenceError) as forbidden:
        validated_rows(claimed, forbidden_capability)
    assert forbidden.value.violation_code == 'FORBIDDEN_CAPABILITY_CLAIM'

    invented_deadline = parse_output({
        'schema_version': '1', 'requirements': [{
            'key': 'meet_deadlines',
            'job_requirement_quote': '按时完成工作或满足截止日期',
            'resume_evidence_quote': None, 'match_status': 'not_reflected',
            'reason': '候选人具备按时完成工作的能力。',
            'confirmation_questions': [],
        }],
    })
    invented_warnings = []
    invented_rows = validated_rows(claimed, invented_deadline, invented_warnings)
    assert invented_rows[0].match_status == 'needs_confirmation'
    assert invented_warnings[0]['violation_detail_code'] == 'NO_EVIDENCE_AFFIRMATIVE'

    uncertain_budget = parse_output({
        'schema_version': '1', 'requirements': [{
            'key': 'annual_budget', 'job_requirement_quote': '负责年度预算编制',
            'resume_evidence_quote': '参与年度预算编制',
            'match_status': 'needs_confirmation',
            'reason': '简历未明确说明是否负责、主导或独立完成年度预算编制。',
            'confirmation_questions': ['是否负责年度预算编制？'],
        }],
    })
    assert len(validated_rows(claimed, uncertain_budget)) == 1

    inflated_budget = parse_output({
        'schema_version': '1', 'requirements': [{
            'key': 'annual_budget', 'job_requirement_quote': '负责年度预算编制',
            'resume_evidence_quote': '参与年度预算编制', 'match_status': 'reflected',
            'reason': '求职者负责年度预算编制。', 'confirmation_questions': [],
        }],
    })
    inflated_warnings = []
    inflated_rows = validated_rows(claimed, inflated_budget, inflated_warnings)
    assert inflated_rows[0].match_status == 'needs_confirmation'
    assert inflated_warnings[0]['violation_code'] == 'RESPONSIBILITY_INFLATION'
    assert inflated_warnings[0]['requirement_key'] == 'annual_budget'

    cases = [
        ({
            **valid_output(evidence='技能：Python\n项目：参与 API 开发'),
        }, 'INVALID_RESUME_QUOTE', 'QUOTE_CROSSES_PARAGRAPH'),
        ({
            'schema_version': '1', 'requirements': [{
                'key': 'r1', 'job_requirement_quote': '要求 Python',
                'resume_evidence_quote': None, 'match_status': 'not_reflected',
                'reason': '该候选人不会 Python。', 'confirmation_questions': [],
            }],
        }, 'FORBIDDEN_CAPABILITY_CLAIM', None),
        ({
            'schema_version': '1', 'requirements': [{
                'key': 'r1', 'job_requirement_quote': '要求 Python',
                'resume_evidence_quote': None, 'match_status': 'not_reflected',
                'reason': '当前简历未体现 Python。',
                'confirmation_questions': ['是否掌握 Python？'],
            }],
        }, 'INVALID_CONFIRMATION_STATE', None),
    ]
    for value, violation_code, detail_code in cases:
        with pytest.raises(EvidenceError) as caught:
            validated_rows(claimed, parse_output(value))
        assert caught.value.violation_code == violation_code
        assert caught.value.violation_detail_code == detail_code
        assert caught.value.requirement_key == 'r1'
        assert caught.value.ordinal == 1


def test_status_schema_and_semantic_review_advisories():
    reflected_without_evidence = {
        'schema_version': '1', 'requirements': [{
            'key': 'r1', 'job_requirement_quote': '要求 Python',
            'resume_evidence_quote': None, 'match_status': 'reflected',
            'reason': '简历原文体现了该要求。', 'confirmation_questions': [],
        }],
    }
    needs_confirmation_without_question = {
        'schema_version': '1', 'requirements': [{
            'key': 'r1', 'job_requirement_quote': '要求 Python',
            'resume_evidence_quote': None, 'match_status': 'needs_confirmation',
            'reason': '当前信息不足。', 'confirmation_questions': [],
        }],
    }
    with pytest.raises(ValidationError):
        parse_output(reflected_without_evidence)
    with pytest.raises(ValidationError):
        parse_output(needs_confirmation_without_question)

    requirements = parse_output(valid_output()).requirements
    supported = FactReviewOutput.model_validate({
        'schema_version': '1',
        'reviews': [{
            'key': 'r1', 'match_verdict': 'supported',
            'reason_verdict': 'supported', 'match_issue': None,
            'reason_issue': None,
        }],
    })
    assert fact_review_advisories(requirements, supported) == []
    unsupported = FactReviewOutput.model_validate({
        'schema_version': '1',
        'reviews': [{
            'key': 'r1', 'match_verdict': 'unsupported',
            'reason_verdict': 'supported', 'match_issue': '证据不足',
            'reason_issue': None,
        }],
    })
    assert fact_review_advisories(requirements, unsupported) == [{
        'code': 'REVIEW_NOT_CONFIRMED', 'key': 'r1',
        'match_verdict': 'unsupported', 'reason_verdict': 'supported',
        'match_issue': '证据不足', 'reason_issue': None,
    }]


def test_fact_review_prompt_requires_complete_semantic_support():
    required_rules = (
        '直接支持岗位要求全部实质内容',
        '只覆盖岗位要求的一部分时，match_verdict 必须为 uncertain',
        '一般会计经验不能证明财务分析或公共会计经验',
        '月度财务报表不能证明月度、年度、KPI 和评论均已满足',
        '预算编制知识不能证明实际编制年度预算',
        'GAAP 理解不能证明研究会计事项以确保 GAAP 合规',
        '相同关键词不等于语义充分支持',
        '不能因为当前 match_status=reflected 就默认 match_verdict=supported',
        'reason 存在额外未支持描述，不能单独降低证据充分的 match_verdict',
    )
    assert all(rule in FACT_REVIEW_PROMPT for rule in required_rules)


def test_semantic_review_downgrades_unconfirmed_requirement(engine):
    app = configured_app(engine)
    with TestClient(app) as client:
        info = login(client).json()
        resume, job = inputs(client, info)
        task = create_analysis(client, info, resume, job, 'unsupported-reason').json()
        process_next(engine, app.state.settings, ModelClient(
            app.state.settings, transport_for(match_review_verdict='unsupported')))
        detail = client.get(f"/api/v1/analyses/{task['id']}").json()
        assert detail['state'] == 'succeeded'
        assert detail['error_code'] is None
        assert detail['requirements']
        assert detail['warnings'][0]['code'] == 'REVIEW_NOT_CONFIRMED'
        requirement = detail['requirements'][0]
        assert requirement['match_status'] == 'needs_confirmation'
        assert requirement['reason'] != '简历原文体现了该要求。'
        assert requirement['reason'] == (
            '简历中存在可核对的原文证据，但该证据只能证明原文所述内容，'
            '尚不能确认其是否完整满足岗位要求。')
        assert requirement['confirmation_questions'] == [
            '请补充说明该简历证据与岗位要求之间的对应关系及尚未明确的部分。']
        assert requirement['resume_evidence_quote'] == '技能：Python'

        reason_resume = client.post('/api/v1/resumes', headers=headers(info), json={
            'title': 'Python 开发简历',
            'original_text': '使用 Python 开发 REST API。',
        }).json()
        reason_job = client.post('/api/v1/jobs', headers=headers(info), json={
            'name': 'Python 工程师', 'description': '熟悉 Python；',
        }).json()
        reason_task = create_analysis(
            client, info, reason_resume, reason_job, 'reason-only-risk').json()
        reason_output = {
            'schema_version': '1',
            'requirements': [{
                'key': 'python_proficiency',
                'job_requirement_quote': '熟悉 Python；',
                'resume_evidence_quote': '使用 Python 开发 REST API。',
                'match_status': 'reflected',
                'reason': '技能列表中列出 Python，因此符合要求。',
                'confirmation_questions': [],
            }],
            'suggestions': [],
        }
        process_next(engine, app.state.settings, ModelClient(
            app.state.settings, transport_for(
                reason_output, match_review_verdict='supported',
                reason_review_verdict='uncertain')))
        reason_detail = client.get(
            f"/api/v1/analyses/{reason_task['id']}").json()
        reviewed = reason_detail['requirements'][0]
        assert reviewed['match_status'] == 'reflected'
        assert reviewed['reason'] == (
            '当前判断仅依据所展示的岗位要求与简历原文证据，'
            '不采用未获证据支持的附加描述。')
        assert '技能列表' not in reviewed['reason']
        assert reviewed['confirmation_questions'] == []

        second = create_analysis(client, info, resume, job, 'review-unavailable').json()
        process_next(engine, app.state.settings, ModelClient(
            app.state.settings, transport_for(review_status=500)))
        second_detail = client.get(f"/api/v1/analyses/{second['id']}").json()
        assert second_detail['state'] == 'succeeded'
        assert second_detail['warnings'] == [
            {'code': 'FACT_REVIEW_UNAVAILABLE'}
        ]


def test_snapshots_are_independent_and_idempotency_is_scoped(engine):
    app = configured_app(engine)
    with TestClient(app) as client:
        info = login(client).json()
        resume, job = inputs(client, info)
        first = create_analysis(client, info, resume, job, 'first').json()
        duplicate = create_analysis(client, info, resume, job, 'first')
        assert duplicate.status_code == 202
        assert duplicate.json()['id'] == first['id']

        updated_job = client.put(f"/api/v1/jobs/{job['id']}", headers=headers(info), json={
            'name': '高级后端工程师', 'description': '要求 Go 和分布式系统',
            'expected_version': 1,
        }).json()
        conflict = create_analysis(client, info, resume, updated_job, 'first')
        assert conflict.status_code == 409
        assert conflict.json()['error']['code'] == 'IDEMPOTENCY_CONFLICT'
        second = create_analysis(client, info, resume, updated_job, 'second').json()
        first_detail = client.get(f"/api/v1/analyses/{first['id']}").json()
        second_detail = client.get(f"/api/v1/analyses/{second['id']}").json()
        assert first_detail['job_description_snapshot'] == '要求 Python，并负责 API 开发'
        assert second_detail['job_description_snapshot'] == '要求 Go 和分布式系统'
        assert first_detail['id'] != second_detail['id']


def test_analysis_account_isolation(engine):
    app = configured_app(engine)
    with TestClient(app) as account_a, TestClient(app) as account_b:
        info_a = login(account_a).json()
        info_b = login(account_b, 'demo_b', 'Demo-B-2026!').json()
        resume, job = inputs(account_a, info_a)
        task = create_analysis(account_a, info_a, resume, job).json()
        assert account_b.get('/api/v1/analyses').json()['items'] == []
        create_other = create_analysis(account_b, info_b, resume, job, 'other-account')
        assert create_other.status_code == 404
        assert account_b.get(f"/api/v1/analyses/{task['id']}").status_code == 404
        retry = account_b.post(f"/api/v1/analyses/{task['id']}/retry",
                               headers=headers(info_b), json={'expected_attempt_no': 1})
        assert retry.status_code == 404
