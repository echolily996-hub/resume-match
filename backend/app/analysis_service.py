import threading
import re
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from .model_client import ModelCallError, ModelClient, resume_segments
from .models import Analysis, AnalysisRequirement, Suggestion, utcnow


class EvidenceError(Exception):
    def __init__(self, violation_code, message, requirement_key=None, ordinal=None,
                 violation_detail_code=None, reason_clause_ordinal=None,
                 absence_context_detected=None, job_context_detected=None,
                 fact_present_in_evidence=None, fact_present_in_job_quote=None,
                 trigger_token=None):
        super().__init__(message)
        self.violation_code = violation_code
        self.requirement_key = requirement_key
        self.ordinal = ordinal
        self.violation_detail_code = violation_detail_code
        self.reason_clause_ordinal = reason_clause_ordinal
        self.resume_evidence_present = None
        self.match_status = None
        self.absence_context_detected = absence_context_detected
        self.job_context_detected = job_context_detected
        self.fact_present_in_evidence = fact_present_in_evidence
        self.fact_present_in_job_quote = fact_present_in_job_quote
        self.trigger_token = trigger_token

    def add_requirement_context(self, requirement_key, ordinal,
                                resume_evidence_present, match_status):
        self.requirement_key = self.requirement_key or requirement_key
        self.ordinal = self.ordinal or ordinal
        if self.resume_evidence_present is None:
            self.resume_evidence_present = resume_evidence_present
        self.match_status = self.match_status or match_status
        return self


@dataclass
class ClaimedAnalysis:
    id: object
    attempt_no: int
    resume_snapshot: str
    job_name_snapshot: str
    job_description_snapshot: str


def claim_next(engine):
    with Session(engine) as db, db.begin():
        item = db.scalar(select(Analysis).where(Analysis.state == 'queued')
                         .order_by(Analysis.created_at, Analysis.id)
                         .with_for_update(skip_locked=True).limit(1))
        if item is None:
            return None
        item.state = 'running'
        item.started_at = utcnow()
        item.finished_at = None
        item.lease_until = utcnow() + timedelta(seconds=240)
        return ClaimedAnalysis(
            item.id, item.attempt_no, item.resume_snapshot,
            item.job_name_snapshot, item.job_description_snapshot,
        )


def quote_span(text: str, quote: str, label: str, violation_code,
               single_paragraph=False):
    start = text.find(quote)
    if start < 0:
        raise EvidenceError(
            violation_code, f'{label}不是输入快照中的连续原文',
            violation_detail_code='QUOTE_NOT_FOUND')
    end = start + len(quote)
    if single_paragraph:
        paragraphs = [(match.start(), match.end()) for match in re.finditer(r'[^\r\n]+', text)]
        if not any(paragraph_start <= start and end <= paragraph_end
                   for paragraph_start, paragraph_end in paragraphs):
            raise EvidenceError(
                violation_code, f'{label}必须来自同一段连续原文',
                violation_detail_code='QUOTE_CROSSES_PARAGRAPH')
    return {'start': start, 'end': end}


NEGATIVE_CAPABILITY = re.compile(
    r'(?:(?:求职者|候选人|用户|该用户|该候选人).{0,12})?'
    r'(?:不会|不懂|不熟悉|未掌握|没有.{0,8}能力|不具备|无法胜任|不能胜任|缺乏.{0,8}(?:能力|经验|技能))',
    re.IGNORECASE,
)
NUMBER_FACT = re.compile(r'(?<![A-Za-z0-9])\d+(?:\.\d+)?%?(?:年|个月|人|次|项|万|亿)?')
ASCII_FACT = re.compile(r'[A-Za-z][A-Za-z0-9]*(?:[.+#/-][A-Za-z0-9+#]+)*')
QUOTED_FACT = re.compile(r'[“「《\"]([^”」》\"]{1,80})[”」》\"]')
CONCRETE_FACT = re.compile(
    r'(?:具备|掌握|熟悉|使用|拥有|具有|积累(?:了)?|参与(?:了)?|完成(?:了)?|实现(?:了)?)'
    r'([^，。；;！？!?\n]{1,60}?)(?:项目经验|经验|技能|能力|职责|经历|项目|业绩|成果)')
NAMED_ENTITY_FACT = re.compile(
    r'(?:参与|负责|主导|完成|任职于|就职于)'
    r'([^，。；;！？!?\n]{1,50}?)(?:公司|项目)')
STRENGTHENING_TERMS = ('独立', '主导', '负责', '精通', '熟练', '管理', '带领', '提升', '降低', '增长')
RESPONSIBILITY_TERMS = frozenset(('独立', '主导', '负责', '管理', '带领'))
UNCERTAIN_RESPONSIBILITY_SCOPE = re.compile(
    r'(?:未明确(?:说明|显示|体现)|未说明|未体现|无法确认|不能确认|不确定|尚不明确)'
    r'[^，,。；;！？!?\n]{0,80}')


def responsibility_term_is_uncertain(clause, term):
    return any(term in match.group(0)
               for match in UNCERTAIN_RESPONSIBILITY_SCOPE.finditer(clause))


def diagnostic_token(value):
    return value.strip()[:32]


def validate_reason_facts(value):
    if value.match_status != 'needs_confirmation' and value.confirmation_questions:
        raise EvidenceError(
            'INVALID_CONFIRMATION_STATE', '仅需要补充确认状态可以包含补充问题')
    if value.match_status != 'reflected' and NEGATIVE_CAPABILITY.search(value.reason):
        raise EvidenceError(
            'FORBIDDEN_CAPABILITY_CLAIM', '未体现或待确认不能断言求职者没有能力')

    evidence = value.resume_evidence_quote
    if evidence is None:
        clauses = re.split(r'[，,。；;！？!?\n]+|但(?:是)?|然而|不过', value.reason)
        for clause_ordinal, clause in enumerate(clauses, start=1):
            absence_context = re.search(
                r'(?:简历|证据|原文).{0,12}'
                r'(?:未明确(?:提及|体现|显示|说明|记录)|未提及|未体现|未说明|未记录|'
                r'未包含|未找到|没有提及|没有体现|不足以确认|无法确认)',
                clause,
            )
            affirmative = re.search(
                r'(?:简历|证据|原文|求职者|候选人|用户).{0,16}'
                r'(?P<trigger>具备|掌握|熟悉|使用|负责|主导|独立|拥有|完成|实现|带领|提升|降低|增长)',
                clause,
            )
            if affirmative and not absence_context:
                trigger = affirmative.group('trigger')
                raise EvidenceError(
                    'UNSUPPORTED_REASON_FACT', '无简历证据时 reason 不能陈述具体简历事实',
                    violation_detail_code='NO_EVIDENCE_AFFIRMATIVE',
                    reason_clause_ordinal=clause_ordinal,
                    absence_context_detected=bool(absence_context),
                    job_context_detected=('岗位' in clause or '要求' in clause),
                    fact_present_in_evidence=False,
                    fact_present_in_job_quote=trigger in value.job_requirement_quote,
                    trigger_token=diagnostic_token(trigger))
        return

    clauses = re.split(r'[。；;！？!?\n]+', value.reason)
    for clause_ordinal, clause in enumerate(clauses, start=1):
        if not clause.strip():
            continue
        resume_context = value.match_status == 'reflected' or any(
            marker in clause for marker in ('简历', '证据', '原文', '求职者', '候选人', '用户'))
        if not resume_context:
            continue
        job_context = '岗位' in clause or '要求' in clause
        absence_context = re.search(
            r'(?:简历|证据|原文).{0,12}'
            r'(?:未明确(?:提及|体现|显示|说明|记录)|未提及|未体现|未说明|未记录|'
            r'未包含|未找到|没有提及|没有体现|不足以确认|无法确认)',
            clause,
        )
        for fact in NUMBER_FACT.findall(clause):
            if fact not in evidence and not (job_context and fact in value.job_requirement_quote):
                raise EvidenceError(
                    'UNSUPPORTED_REASON_FACT', 'reason 中的数字事实没有简历证据支持',
                    violation_detail_code='UNSUPPORTED_NUMBER',
                    reason_clause_ordinal=clause_ordinal,
                    absence_context_detected=bool(absence_context),
                    job_context_detected=job_context,
                    fact_present_in_evidence=fact in evidence,
                    fact_present_in_job_quote=fact in value.job_requirement_quote,
                    trigger_token=diagnostic_token(fact))
        for fact in ASCII_FACT.findall(clause):
            if fact.lower() in {'resume', 'jd'}:
                continue
            if fact not in evidence and not (job_context and fact in value.job_requirement_quote):
                raise EvidenceError(
                    'UNSUPPORTED_REASON_FACT', 'reason 中的技能或名称没有简历证据支持',
                    violation_detail_code='UNSUPPORTED_ASCII_FACT',
                    reason_clause_ordinal=clause_ordinal,
                    absence_context_detected=bool(absence_context),
                    job_context_detected=job_context,
                    fact_present_in_evidence=fact in evidence,
                    fact_present_in_job_quote=fact in value.job_requirement_quote,
                    trigger_token=diagnostic_token(fact))
        for fact in QUOTED_FACT.findall(clause):
            if fact not in evidence and not (job_context and fact in value.job_requirement_quote):
                raise EvidenceError(
                    'UNSUPPORTED_REASON_FACT', 'reason 中引用的具体事实没有简历证据支持',
                    violation_detail_code='UNSUPPORTED_QUOTED_FACT',
                    reason_clause_ordinal=clause_ordinal,
                    absence_context_detected=bool(absence_context),
                    job_context_detected=job_context,
                    fact_present_in_evidence=fact in evidence,
                    fact_present_in_job_quote=fact in value.job_requirement_quote,
                    trigger_token=diagnostic_token(fact))
        for pattern, detail_code in (
                (CONCRETE_FACT, 'UNSUPPORTED_CONCRETE_FACT'),
                (NAMED_ENTITY_FACT, 'UNSUPPORTED_NAMED_ENTITY')):
            for fact in pattern.findall(clause):
                fact = re.sub(r'^(?:了|相关|该|此)+|(?:相关|方面|的)+$', '', fact.strip())
                if fact and fact not in evidence and not (
                        job_context and fact in value.job_requirement_quote):
                    raise EvidenceError(
                        'UNSUPPORTED_REASON_FACT',
                        'reason 中的经历、技能或名称没有简历证据支持',
                        violation_detail_code=detail_code,
                        reason_clause_ordinal=clause_ordinal,
                        absence_context_detected=bool(absence_context),
                        job_context_detected=job_context,
                        fact_present_in_evidence=fact in evidence,
                        fact_present_in_job_quote=fact in value.job_requirement_quote,
                        trigger_token=diagnostic_token(fact))
        for term in STRENGTHENING_TERMS:
            if term in clause and term not in evidence and not (
                    job_context and term in value.job_requirement_quote):
                if (term in RESPONSIBILITY_TERMS and
                        responsibility_term_is_uncertain(clause, term)):
                    continue
                raise EvidenceError(
                    'RESPONSIBILITY_INFLATION', 'reason 扩大了简历中的职责、能力或成果',
                    reason_clause_ordinal=clause_ordinal)


def fact_review_advisories(requirements, review_output):
    expected_keys = [item.key for item in requirements]
    actual = {item.key: item for item in review_output.reviews}
    advisories = []
    if set(actual) != set(expected_keys) or len(actual) != len(expected_keys):
        advisories.append({'code': 'REVIEW_KEY_MISMATCH'})
    for key in expected_keys:
        review = actual.get(key)
        if review is not None and (
                review.match_verdict != 'supported' or
                review.reason_verdict != 'supported'):
            advisories.append({
                'code': 'REVIEW_NOT_CONFIRMED', 'key': key,
                'match_verdict': review.match_verdict,
                'reason_verdict': review.reason_verdict,
                'match_issue': review.match_issue,
                'reason_issue': review.reason_issue,
            })
    return advisories


def apply_fact_review(rows, review_output):
    rows_by_key = {row.requirement_key: row for row in rows}
    for review in review_output.reviews:
        row = rows_by_key.get(review.key)
        if row is None:
            continue
        if review.match_verdict != 'supported':
            row.match_status = 'needs_confirmation'
            if row.resume_quote:
                row.reason = (
                    '简历中存在可核对的原文证据，但该证据只能证明原文所述内容，'
                    '尚不能确认其是否完整满足岗位要求。')
                row.confirmation_questions = [
                    '请补充说明该简历证据与岗位要求之间的对应关系及尚未明确的部分。']
            else:
                row.reason = '当前简历没有可核对的直接证据，尚不能确认是否满足岗位要求。'
                row.confirmation_questions = [
                    '请补充能够证明是否满足该岗位要求的经历或信息。']
        elif review.reason_verdict != 'supported':
            row.reason = '当前判断仅依据所展示的岗位要求与简历原文证据，不采用未获证据支持的附加描述。'


SEMANTIC_WARNING_DETAILS = frozenset((
    'NO_EVIDENCE_AFFIRMATIVE', 'UNSUPPORTED_ASCII_FACT',
    'UNSUPPORTED_CONCRETE_FACT', 'UNSUPPORTED_NAMED_ENTITY',
))


def is_semantic_warning(exc):
    return (exc.violation_code == 'RESPONSIBILITY_INFLATION' or
            (exc.violation_code == 'UNSUPPORTED_REASON_FACT' and
             exc.violation_detail_code in SEMANTIC_WARNING_DETAILS))


def evidence_diagnostic(exc):
    diagnostic = {
        'violation_code': exc.violation_code,
        'requirement_key': exc.requirement_key,
        'ordinal': exc.ordinal,
        'violation_detail_code': exc.violation_detail_code,
        'resume_evidence_present': exc.resume_evidence_present,
        'match_status': exc.match_status,
        'absence_context_detected': exc.absence_context_detected,
        'job_context_detected': exc.job_context_detected,
        'fact_present_in_evidence': exc.fact_present_in_evidence,
        'fact_present_in_job_quote': exc.fact_present_in_job_quote,
        'trigger_token': exc.trigger_token,
    }
    if exc.reason_clause_ordinal is not None:
        diagnostic['reason_clause_ordinal'] = exc.reason_clause_ordinal
    return diagnostic


def validated_rows(claimed: ClaimedAnalysis, output, warnings=None):
    rows = []
    warnings = warnings if warnings is not None else []
    forbidden_claims = ('用户不会', '求职者不会', '不具备该能力', '没有该能力')
    for ordinal, value in enumerate(output.requirements, start=1):
        row_match_status = value.match_status
        row_reason = value.reason
        row_questions = value.confirmation_questions
        row_resume_quote = value.resume_evidence_quote
        resume_span = None
        try:
            job_span = quote_span(
                claimed.job_description_snapshot, value.job_requirement_quote,
                '岗位要求引文', 'INVALID_JOB_QUOTE')

            invalid_resume_quote = False
            if row_resume_quote is not None:
                try:
                    resume_span = quote_span(
                        claimed.resume_snapshot, row_resume_quote,
                        '简历证据引文', 'INVALID_RESUME_QUOTE',
                        single_paragraph=True)
                except EvidenceError as exc:
                    exc.add_requirement_context(
                        value.key,
                        ordinal,
                        True,
                        value.match_status,
                    )
                    if exc.violation_code != 'INVALID_RESUME_QUOTE':
                        raise
                    warnings.append({
                        'code': 'INVALID_RESUME_QUOTE_DOWNGRADED',
                        **evidence_diagnostic(exc),
                    })
                    invalid_resume_quote = True
                    row_resume_quote = None
                    resume_span = None
                    row_match_status = 'needs_confirmation'
                    row_reason = (
                        '模型返回的简历证据无法作为单段连续原文核验，'
                        '现有信息不足以确认该岗位要求。'
                    )
                    row_questions = [
                        '请补充一段能够单独、完整证明该岗位要求的简历经历或信息。'
                    ]

            if not invalid_resume_quote:
                if value.match_status != 'reflected' and any(
                        phrase in value.reason for phrase in forbidden_claims):
                    raise EvidenceError(
                        'FORBIDDEN_CAPABILITY_CLAIM',
                        '未体现不能断言求职者没有能力')
                validate_reason_facts(value)
        except EvidenceError as exc:
            exc.add_requirement_context(
                value.key, ordinal, value.resume_evidence_quote is not None,
                value.match_status)
            if not is_semantic_warning(exc):
                raise exc
            warnings.append({'code': 'SEMANTIC_EVIDENCE_WARNING',
                             **evidence_diagnostic(exc)})
            row_match_status = 'needs_confirmation'
            row_reason = '现有简历证据不足以确认该岗位要求，需要补充确认。'
            row_questions = (value.confirmation_questions or
                             ['请补充说明是否具备该岗位要求对应的经验或能力。'])
        rows.append(AnalysisRequirement(
            analysis_id=claimed.id,
            ordinal=ordinal,
            requirement_key=value.key,
            job_quote=value.job_requirement_quote,
            job_span=job_span,
            resume_quote=row_resume_quote,
            resume_span=resume_span,
            match_status=row_match_status,
            reason=row_reason,
            confirmation_questions=row_questions,
        ))
    return rows


def _new_ascii_facts(original, suggested):
    original_facts = {
        value.casefold()
        for value in ASCII_FACT.findall(original)
    }
    return {
        value.casefold()
        for value in ASCII_FACT.findall(suggested)
        if value.casefold() not in original_facts
    }


def _new_number_facts(original, suggested):
    original_facts = set(NUMBER_FACT.findall(original))
    return {
        value
        for value in NUMBER_FACT.findall(suggested)
        if value not in original_facts
    }


def _adds_responsibility_inflation(original, suggested):
    for term in RESPONSIBILITY_TERMS:
        if term in original or term not in suggested:
            continue
        if term == '管理':
            remaining = re.sub(
                r'管理[^，,。；;！？!？\n]{0,40}(?:数据|信息|记录)',
                '',
                suggested,
            )
            if term not in remaining:
                continue
        return True
    return False


def _adds_capability_inflation(original, suggested):
    return any(
        term in suggested and term not in original
        for term in ('精通', '熟练', '专家')
    )


def build_suggestions(resume_text, rows, candidates, suggestion_reviews=None):
    warnings = []
    suggestion_reviews = suggestion_reviews or {}

    rows_by_key = {
        row.requirement_key: row
        for row in rows
    }

    segments_by_id = {
        segment['id']: segment['text']
        for segment in resume_segments(resume_text)
    }

    # 一个 source_segment_id 最终只允许持久化一条 Suggestion。
    # value = (ranking, Suggestion)
    selected_by_segment = {}

    for candidate_index, candidate in enumerate(candidates):
        row = rows_by_key[candidate.requirement_key]
        original = segments_by_id.get(candidate.source_segment_id)

        if original is None:
            warnings.append({
                'code': 'SUGGESTION_BLOCKED',
                'requirement_key': row.requirement_key,
                'source_segment_id': candidate.source_segment_id,
                'reason': 'SOURCE_SEGMENT_NOT_FOUND',
            })
            continue

        source_matches = original == candidate.original_passage
        rendered = f'- {original}'
        is_bullet = candidate.template_id == 'markdown-bullet-v1'
        is_faithful = candidate.template_id == 'faithful-rewrite-v1'
        suggested = candidate.suggested_passage

        fact_support_failures = []

        for ordinal, support in enumerate(
            candidate.fact_support,
            start=1,
        ):
            codes = []

            if support.source_quote not in original:
                codes.append('SOURCE_QUOTE_NOT_IN_ORIGINAL')

            if is_bullet and support.proposed_fact != support.source_quote:
                codes.append('PROPOSED_FACT_DIFFERS_FROM_SOURCE')

            if codes:
                fact_support_failures.append({
                    'ordinal': ordinal,
                    'codes': codes,
                })

        support_is_local = all(
            support.source_quote in original
            and (
                not is_bullet
                or support.proposed_fact == support.source_quote
            )
            for support in candidate.fact_support
        )

        blocked_detail_codes = []

        if row.match_status != 'reflected':
            blocked_detail_codes.append(
                'REQUIREMENT_NOT_REFLECTED'
            )

        if not source_matches:
            blocked_detail_codes.append(
                'SOURCE_PASSAGE_MISMATCH'
            )

        if not (is_bullet or is_faithful):
            blocked_detail_codes.append(
                'TEMPLATE_ID_MISMATCH'
            )

        if is_bullet and candidate.suggested_passage != rendered:
            blocked_detail_codes.append(
                'RENDERED_TEXT_MISMATCH'
            )

        if not support_is_local:
            blocked_detail_codes.append(
                'FACT_SUPPORT_NOT_LOCAL'
            )

        already_markdown = original.lstrip().startswith(
            ('- ', '* ', '+ ')
        )

        if is_bullet and already_markdown:
            blocked_detail_codes.append(
                'ALREADY_MARKDOWN_LIST'
            )

        faithful_hard_failure = False
        if is_faithful:
            faithful_checks = (
                ('SUGGESTED_TEXT_EMPTY', not suggested.strip()),
                ('SUGGESTED_TEXT_UNCHANGED', suggested == original),
                ('FACT_SUPPORT_MISSING', not candidate.fact_support),
                (
                    'UNSUPPORTED_NUMBER',
                    bool(_new_number_facts(original, suggested)),
                ),
                (
                    'UNSUPPORTED_ASCII_FACT',
                    bool(_new_ascii_facts(original, suggested)),
                ),
                (
                    'RESPONSIBILITY_INFLATION',
                    _adds_responsibility_inflation(original, suggested),
                ),
                (
                    'CAPABILITY_INFLATION',
                    _adds_capability_inflation(original, suggested),
                ),
            )
            for code, failed in faithful_checks:
                if failed:
                    blocked_detail_codes.append(code)
                    faithful_hard_failure = True

            review_verdict = suggestion_reviews.get(candidate_index)
            if review_verdict != 'supported':
                blocked_detail_codes.append(
                    'SUGGESTION_REVIEW_UNAVAILABLE'
                    if review_verdict is None
                    else 'SUGGESTION_REVIEW_NOT_SUPPORTED'
                )

        # 只有受约束模板或通过两层校验的事实守恒改写才能进入 draft。
        evidence_is_local = (
            row.resume_quote is not None
            and row.resume_quote in original
        )
        bullet_actionable = (
            is_bullet
            and row.match_status == 'reflected'
            and evidence_is_local
            and not already_markdown
        )
        faithful_actionable = (
            is_faithful
            and row.match_status == 'reflected'
            and evidence_is_local
            and source_matches
            and support_is_local
            and not faithful_hard_failure
            and suggestion_reviews.get(candidate_index) == 'supported'
        )
        actionable = bullet_actionable or faithful_actionable

        blocked_reason = (
            None
            if actionable
            else '该建议无法通过受约束模板和局部事实校验，请手工核对。'
        )

        suggestion = Suggestion(
            analysis_id=row.analysis_id,
            requirement_id=row.id,
            source_segment_id=candidate.source_segment_id,
            original_text=original,
            suggested_text=(
                rendered
                if bullet_actionable
                else (suggested if faithful_actionable else None)
            ),
            rewrite_reason=(
                (
                    '仅调整已有原文的展示格式，不新增或改写事实。'
                    if bullet_actionable
                    else (
                        '在不新增简历事实的前提下优化已有表述，'
                        '使表达更清晰并更贴合岗位要求。'
                    )
                )
                if actionable else
                '建议候选未通过安全校验，不能直接写入修改稿。'
            ),
            template_id=candidate.template_id,
            template_args=(
                {'prefix': '- '}
                if is_bullet
                else candidate.template_args.model_dump()
            ),
            validation_status=(
                'actionable'
                if actionable
                else 'blocked'
            ),
            blocked_reason=blocked_reason,
            blocked_detail_codes=blocked_detail_codes,
            fact_support_failures=fact_support_failures,
            decision='pending',
        )

        # 同一简历段落有多个候选时：
        # 1. actionable 优先于 blocked
        # 2. 同等级时，岗位要求 ordinal 小的优先
        # 3. requirement_key 作为稳定 tie-break
        ranking = (
            0 if actionable else 1,
            row.ordinal,
            row.requirement_key,
        )

        existing = selected_by_segment.get(
            candidate.source_segment_id
        )

        if existing is None or ranking < existing[0]:
            selected_by_segment[candidate.source_segment_id] = (
                ranking,
                suggestion,
            )

    # 按简历段落顺序稳定输出 p1、p2、p3……
    suggestions = [
        selected_by_segment[segment_id][1]
        for segment_id in sorted(
            selected_by_segment,
            key=lambda value: int(value[1:]),
        )
    ]

    return suggestions, warnings
    


def finish_success(engine, claimed, result, rows, warnings=None,
                   suggestion_reviews=None):
    with Session(engine) as db, db.begin():
        item = db.get(Analysis, claimed.id)

        if (
            not item
            or item.state != 'running'
            or item.attempt_no != claimed.attempt_no
        ):
            return False

        db.execute(
            delete(AnalysisRequirement).where(
                AnalysisRequirement.analysis_id == claimed.id
            )
        )

        db.add_all(rows)
        db.flush()

        

        suggestions, suggestion_warnings = build_suggestions(
            claimed.resume_snapshot,
            rows,
            result.output.suggestions,
            suggestion_reviews,
        )

        db.add_all(suggestions)

        item.state = 'succeeded'
        item.model_metadata = result.metadata
        item.error_code = None
        item.error_message = None
        item.warnings = (warnings or []) + suggestion_warnings
        item.lease_until = None
        item.finished_at = utcnow()

        return True


def finish_failure(engine, claimed, code, message, metadata=None):
    with Session(engine) as db, db.begin():
        item = db.get(Analysis, claimed.id)
        if not item or item.state != 'running' or item.attempt_no != claimed.attempt_no:
            return False
        item.state = 'failed'
        item.error_code = code
        item.error_message = message
        item.model_metadata = metadata or None
        item.lease_until = None
        item.finished_at = utcnow()
        return True


def process_next(engine, settings, client=None):
    claimed = claim_next(engine)
    if claimed is None:
        return False
    client = client or ModelClient(settings)
    result = None
    try:
        result = client.analyze(
            claimed.resume_snapshot, claimed.job_name_snapshot,
            claimed.job_description_snapshot, claimed.attempt_no)
        validation_warnings = []
        rows = validated_rows(claimed, result.output, validation_warnings)
        if validation_warnings:
            result.metadata['evidence_warnings'] = validation_warnings
        review_warnings = []
        try:
            review_result = client.review_facts(
                result.output.requirements, claimed.attempt_no)
            review_warnings = fact_review_advisories(
                result.output.requirements, review_result.output)
            apply_fact_review(rows, review_result.output)
            result.metadata['fact_review'] = {
                **review_result.metadata, 'status': 'advisory',
                'findings': review_warnings,
            }
        except ModelCallError as exc:
            review_warnings = [{'code': 'FACT_REVIEW_UNAVAILABLE'}]
            result.metadata['fact_review'] = {
                **exc.metadata, 'status': 'unavailable', 'error_code': exc.code,
            }

        suggestion_reviews = {}
        suggestion_review_warnings = []
        faithful_candidates = [
            (index, candidate)
            for index, candidate in enumerate(result.output.suggestions)
            if candidate.template_id == 'faithful-rewrite-v1'
        ]
        if faithful_candidates:
            try:
                suggestion_review_result = client.review_suggestions(
                    [candidate for _, candidate in faithful_candidates],
                    claimed.attempt_no,
                )
                reviews_by_ordinal = {
                    review.ordinal: review
                    for review in suggestion_review_result.output.reviews
                }
                for review_ordinal, (candidate_index, _) in enumerate(
                    faithful_candidates,
                    start=1,
                ):
                    review = reviews_by_ordinal.get(review_ordinal)
                    if review is not None:
                        suggestion_reviews[candidate_index] = review.verdict
                    if review is None or review.verdict != 'supported':
                        suggestion_review_warnings.append({
                            'code': 'SUGGESTION_FACT_REVIEW_WARNING',
                            'ordinal': review_ordinal,
                            'verdict': (
                                review.verdict if review is not None
                                else 'missing'
                            ),
                        })
                result.metadata['suggestion_review'] = {
                    **suggestion_review_result.metadata,
                    'status': 'completed',
                    'reviewed_count': len(faithful_candidates),
                }
            except (ModelCallError, AttributeError) as exc:
                suggestion_review_warnings = [{
                    'code': 'SUGGESTION_REVIEW_UNAVAILABLE'
                }]
                result.metadata['suggestion_review'] = {
                    **(exc.metadata if isinstance(exc, ModelCallError) else {}),
                    'status': 'unavailable',
                    'error_code': (
                        exc.code
                        if isinstance(exc, ModelCallError)
                        else 'REVIEWER_NOT_AVAILABLE'
                    ),
                }
        finish_success(
            engine,
            claimed,
            result,
            rows,
            validation_warnings + review_warnings + suggestion_review_warnings,
            suggestion_reviews,
        )
    except EvidenceError as exc:
        metadata = dict(result.metadata) if result else None
        if metadata is not None:
            metadata.update(evidence_diagnostic(exc))
        finish_failure(engine, claimed, 'MODEL_INVALID_EVIDENCE',
                       '模型返回的事实或引文无法通过证据校验。',
                       metadata)
    except ModelCallError as exc:
        metadata = exc.metadata
        if result is not None:
            metadata = dict(result.metadata)
            metadata['fact_review'] = exc.metadata
        finish_failure(engine, claimed, exc.code, exc.message, metadata)
    except Exception:
        finish_failure(engine, claimed, 'ANALYSIS_INTERNAL_ERROR', '分析处理失败，请稍后重试。')
    return True


def recover_interrupted(engine):
    with Session(engine) as db, db.begin():
        db.execute(update(Analysis).where(
            Analysis.state == 'running',
            Analysis.lease_until < utcnow(),
        ).values(
            state='failed', error_code='TASK_INTERRUPTED',
            error_message='分析任务执行中断，请重试。', finished_at=utcnow(), lease_until=None,
        ))


class AnalysisWorker:
    def __init__(self, engine, settings):
        self.engine = engine
        self.settings = settings
        self.stop_event = threading.Event()
        self.wake_event = threading.Event()
        self.thread = threading.Thread(target=self.run, name='analysis-worker', daemon=True)

    def start(self):
        recover_interrupted(self.engine)
        self.thread.start()

    def wake(self):
        self.wake_event.set()

    def stop(self):
        self.stop_event.set()
        self.wake_event.set()
        if self.thread.is_alive():
            self.thread.join(timeout=5)

    def run(self):
        while not self.stop_event.is_set():
            if not process_next(self.engine, self.settings):
                recover_interrupted(self.engine)
                self.wake_event.wait(1)
                self.wake_event.clear()
