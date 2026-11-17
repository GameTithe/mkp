"""Author-created, uncalibrated quality fixtures. Optional live checks use the
same router/judge/reviewer paths; they do NOT certify human evaluation accuracy.
"""
from __future__ import annotations
import copy
import json
import time
from pathlib import Path
from .storage import write_json,canonical
from .evaluation import Evaluator,parse_json
from .rubric import RUBRIC,ANCHORS,TOP_ANCHORS
from .quality import review_axis
from .codex import Codex
from .contextual import POLICY,route_schema,validate_route,render_route


def load(exam):
    data=json.loads((exam.root/'quality/benchmarks.json').read_text(encoding='utf-8'))
    problem=exam.problem()
    route_file=problem.manifest.get('stakeholder_benchmark') if problem else None
    if route_file:
        from .fs import safe_path
        data['stakeholder_cases']=json.loads(safe_path(problem.path,route_file).read_text(encoding='utf-8'))['cases']
    else:
        data['stakeholder_cases']=[]
    if data.get('human_reviewed') is not False:raise ValueError('기준 사례를 사람 검증으로 오표시할 수 없습니다. 현재 번들은 미교정 자료입니다.')
    ids=[x['id'] for x in data['judge_cases']+data['stakeholder_cases']]
    if len(ids)!=len(set(ids)):raise ValueError('벤치마크 ID 중복')
    for case in data['judge_cases']:
        if case['axis'] not in RUBRIC or case['criterion'] not in RUBRIC[case['axis']]['criteria']:raise ValueError('기준 사례 평가축 오류')
        if not case['atoms'] or len({a['id'] for a in case['atoms']})!=len(case['atoms']):raise ValueError('기준 근거 오류')
        if not isinstance(case['expected_ratings'],list) or not case['expected_ratings'] or any(x is not None and (type(x)is not int or not 0<=x<=4) for x in case['expected_ratings']):raise ValueError('예상 점수 범위 오류')
    truth=exam.problem().truth() if exam.problem() else None
    if truth:
        allowed={f['id'] for f in truth['facts']}
        for case in data['stakeholder_cases']:
            if not set(case['must_include']+case['must_exclude'])<=allowed:raise ValueError('담당자 검사 조항 오류')
    return data


def validate(exam):
    data=load(exam)
    return {'fixture_valid':True,'judge_cases':len(data['judge_cases']),'stakeholder_cases':len(data['stakeholder_cases']),
            'model_called':False,'human_reviewed':False,'warning':'형식 검사만 통과. 실제 모델의 정확성/공정성 인증 아님.'}


class _Live:
    def __init__(self,exam):
        self.root,self.store,self.config=exam.root,exam.store,exam.config
    def provider(self):return Codex(self.root,self.store,self.config)
    def session(self):return {'mode':'llm'}
    def prompt(self,name):return (self.root/'prompts'/f'{name}.md').read_text(encoding='utf-8')


def summarize_judge_attempts(case, attempts, repeats):
    """A raw match is diagnostic only; errors/holds can never be a verified pass."""
    expected = case['expected_ratings']
    raw = [x['raw_rating'] for x in attempts if x.get('raw_available')]
    reviewed = [x['review'] for x in attempts if x.get('review') is not None]
    errors = [x['error'] for x in attempts if x.get('error')]
    errors += [r.get('semantic_review', {}).get('reason', '재검토 실패') for r in reviewed
               if 'review_error' in r.get('hold_reasons', []) or r.get('hold_reason') == 'review_error']
    eligible = lambda c: c.get('review_status') in {'verified', 'adjudicated_boundary'} or (
        c.get('review_status') == 'unobserved' and c.get('rating') is None and c.get('draft_rating') is None)
    raw_match = len(raw) == repeats and all(x in expected for x in raw)
    final_ratings = [c['rating'] for c in reviewed]
    reviewed_match = (not errors and len(reviewed) == repeats and all(eligible(c) for c in reviewed)
                      and all(x in expected for x in final_ratings))
    if errors:
        status = 'error'
    elif len(reviewed) != repeats:
        status = 'incomplete'
    elif not all(eligible(c) for c in reviewed):
        status = 'review_hold'
    elif not reviewed_match:
        status = 'mismatch'
    else:
        status = 'expected_unobserved' if all(x is None for x in final_ratings) else 'pass'
    return {'id': case['id'], 'expected_ratings': expected, 'attempts': attempts,
            'ratings': raw, 'raw_ratings': raw, 'reviewed_ratings': final_ratings, 'reviews': reviewed,
            'raw_match': raw_match, 'reviewed_match': reviewed_match,
            'matches_authored_fixture': reviewed_match,
            'status': status, 'repeat_disagreement': len(set(raw)) > 1,
            'reviewed_repeat_disagreement': len(set(final_ratings)) > 1, 'errors': errors}


def run(exam, repeats=2, scope='all'):
    if type(repeats) is not int or not 1 <= repeats <= 3:
        raise ValueError('반복은 1~3회입니다.')
    if scope not in {'all', 'judge', 'stakeholder'}:
        raise ValueError('scope: all / judge / stakeholder')
    state = exam.store.get('session')
    if state and state['phase'] not in {'completed', 'evaluated'}:
        raise RuntimeError('기준 사례는 시험 시작 전 또는 시험 종료 후에만 실행합니다. 비용이 발생합니다.')
    data = load(exam); live = _Live(exam)
    folder = exam.root / 'runtime/quality'; folder.mkdir(parents=True, exist_ok=True)
    output = {'created_at': time.time(), 'mode': 'live', 'human_reviewed': False, 'repeats': repeats,
              'scope': scope, 'judge': [], 'stakeholder': [], 'model': exam.config.get('model'),
              'limitations': ['출제자 작성 기준 사례와의 일치 검사이며 사람 기준과 교정되지 않았습니다.',
                             '같은 모델의 반복 일치는 정확성과 같지 않습니다. 실제 계정 사용량이 발생합니다.',
                             'raw_match는 최초 초안만 검사합니다. 통과 집계에는 reviewed_match만 사용합니다.']}
    dest = exam.root / 'quality_runs' / (time.strftime('%Y%m%d-%H%M%S') + '-' + str(time.time_ns())[-7:]) / 'report.json'
    if scope in {'all', 'judge'}:
        for case in data['judge_cases']:
            bundle = {'atoms': case['atoms'], 'coverage': {'synthetic': True}}
            attempts = []
            for iteration in range(repeats):
                item = {'iteration': iteration + 1, 'raw_available': False, 'review': None, 'error': None}
                payload = live.prompt('judge') + '\nAXIS=' + case['axis'] + '\n평가기준:\n' + canonical(RUBRIC[case['axis']]) + canonical(ANCHORS) + canonical(TOP_ANCHORS) + '\n<untrusted_evidence>\n' + canonical(bundle) + '\n</untrusted_evidence>'
                try:
                    response = live.provider().run(payload, folder, 'benchmark:judge', schema=exam.root / 'schemas/axis_score.json')
                    grade = Evaluator(live).validate_axis(parse_json(response['text']), case['axis'], bundle)
                    selected = next(x for x in grade['criteria'] if x['id'] == case['criterion'])
                    item.update(raw_available=True, raw_rating=selected['rating'])
                    reviewed = review_axis(live, grade, bundle, [])
                    item['review'] = next(x for x in reviewed['criteria'] if x['id'] == case['criterion'])
                except Exception as exc:
                    item['error'] = type(exc).__name__ + ': ' + str(exc)
                attempts.append(item)
            output['judge'].append(summarize_judge_attempts(case, attempts, repeats)); write_json(dest, output)
    if scope in {'all', 'stakeholder'}:
        problem = exam.problem(); truth = problem.truth()
        sp = folder / 'context_route.json'; write_json(sp, route_schema(truth['facts']))
        for case in data['stakeholder_cases']:
            body = {'persona': truth['persona'], 'facts': truth['facts'], 'question': case['question'],
                    'history': case.get('history', []), 'known_fact_ids': case.get('known_fact_ids', [])}
            attempts = []
            for iteration in range(repeats):
                item = {'iteration': iteration + 1, 'matches_authored_fixture': False}
                try:
                    answer = live.provider().run(POLICY + '\nCONTEXT_INPUT_JSON\n' + canonical(body), folder,
                                                 'benchmark:stakeholder', schema=sp)
                    route = validate_route(parse_json(answer['text']), truth)
                    text, ids, deferred = render_route(route, truth, body['known_fact_ids'], body['history'])
                    item.update(route=route, disclosed=ids, text=text, matches_authored_fixture=
                        route['intent'] in case['expected_intents'] and set(case['must_include']) <= set(ids)
                        and not set(case['must_exclude']) & set(ids))
                except Exception as exc:
                    item.update(error=type(exc).__name__ + ': ' + str(exc))
                attempts.append(item)
            matched = all(x['matches_authored_fixture'] for x in attempts) and len(attempts) == repeats
            item = {'id': case['id'], 'group': case.get('group', 'base'), 'attempts': attempts,
                    'matches_authored_fixture': matched,
                    'status': 'error' if any(x.get('error') for x in attempts) else ('pass' if matched else 'mismatch'),
                    'repeat_disagreement': len({canonical({'route': x.get('route', {}).get('intent'), 'disclosed': sorted(x.get('disclosed', []))}) for x in attempts}) > 1}
            output['stakeholder'].append(item); write_json(dest, output)
    rows = output['judge'] + output['stakeholder']
    output['fixture_match_count'] = sum(x['matches_authored_fixture'] for x in rows)
    output['fixture_total'] = len(rows)
    output['status_counts'] = {status: sum(x['status'] == status for x in rows)
                               for status in ('pass', 'expected_unobserved', 'review_hold', 'mismatch', 'error', 'incomplete')}
    output['raw_judge_match_count'] = sum(x['raw_match'] for x in output['judge'])
    output['reviewed_judge_match_count'] = sum(x['reviewed_match'] for x in output['judge'])
    output['certified_accuracy'] = None; output['report_path'] = str(dest.relative_to(exam.root)); write_json(dest, output)
    return output
