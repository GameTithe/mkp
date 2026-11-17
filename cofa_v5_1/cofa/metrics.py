"""Usage evidence, not a score or billing estimate.
Only provider-reported numbers are used. Stream and final-summary usage are not
counted twice; simulator overhead is separated from candidate development.
"""
from __future__ import annotations


def _count(value):
    return value if type(value) is int and value >= 0 else None


def normalize_usage(raw):
    if not isinstance(raw, dict):
        return None
    incoming = _count(raw.get('input_tokens'))
    outgoing = _count(raw.get('output_tokens'))
    if incoming is None or outgoing is None:
        return None
    details = raw.get('input_tokens_details')
    cache = raw.get('cached_input_tokens', details.get('cached_tokens') if isinstance(details, dict) else None)
    cache = _count(cache)
    if cache is not None and cache > incoming:
        return None
    return {'input_tokens': incoming, 'cached_input_tokens': cache,
            'output_tokens': outgoing, 'total_tokens': incoming + outgoing}


def role_group(role):
    if role in ('main', 'auditor') or role.startswith('worker:'):
        return 'development'
    if role == 'doctor':
        return 'setup'
    if role in ('requirements', 'feedback', 'coaching', 'stakeholder', 'interviewer', 'evidence_mapper', 'mapper') or role.startswith(('judge:', 'evidence:', 'summary:')):
        return 'assessment'
    # The mapper uses chunk-specific names; other internal calls are still not development.
    return 'assessment' if role.startswith(('map:', 'reduce:', 'interview', 'evidence-review:', 'benchmark:', 'stakeholder:', 'boundary-review:')) else 'other'


def usage_report(store):
    events = store.events()
    starts = [e['seq'] for e in events if e['kind'] == 'session_start']
    if starts:
        events = [e for e in events if e['seq'] >= starts[-1]]
    submissions = [e['seq'] for e in events if e['kind'] == 'submission']
    submitted_seq = submissions[0] if submissions else None
    calls = {}
    for event in events:
        if event['kind'] not in ('llm_request', 'llm_complete', 'codex_event'):
            continue
        data = event['data']; ident = data.get('call_id')
        if not ident:
            continue
        automatic_audit = event['channel'] == 'auditor' and submitted_seq is not None and event['seq'] > submitted_seq
        group = 'assessment' if automatic_audit else role_group(event['channel'])
        item = calls.setdefault(ident, {'call_id': ident, 'role': event['channel'],
            'group': group, 'start': None, 'end': None,
            'demo': False, 'stream_usage': [], 'final_usage': None,
            'state': 'unfinished', 'evidence_ids': []})
        item['demo'] = item['demo'] or bool(data.get('demo'))
        if event['kind'] == 'llm_request':
            item['start'] = event['at']
        elif event['kind'] == 'llm_complete':
            item['end'] = event['at']; item['final_usage'] = normalize_usage(data.get('usage'))
            item['state'] = 'failed' if data.get('returncode') != 0 or data.get('timed_out') or data.get('interrupted') or data.get('capture_errors') else 'completed'
            item['evidence_ids'].append(event['id'])
        elif data.get('event', {}).get('type') == 'turn.completed':
            # A malformed stream row is counted as unmeasured rather than discarded.
            item['stream_usage'].append(normalize_usage(data['event'].get('usage')))
            item['evidence_ids'].append(event['id'])
    labels = {'development': '응시자 개발 (메인·병렬·개발 중 검토)',
              'assessment': '환경 운영 (담당자·제출 후 검토·면담·분석·채점)',
              'setup': '연결 점검', 'other': '분류되지 않은 호출'}
    groups = {}
    details = []
    for group, label in labels.items():
        members = [c for c in calls.values() if c['group'] == group]
        incoming = outgoing = cached = measured = measured_cache = unmeasured_turns = 0
        failed = demo = unfinished = 0
        elapsed = 0.0
        for c in members:
            sources = c['stream_usage'] if c['stream_usage'] else [c['final_usage']]
            valid = [u for u in sources if u is not None] if not c['demo'] else []
            unknown = len(sources) - len(valid)
            unmeasured_turns += unknown
            if valid:
                measured += 1
                incoming += sum(u['input_tokens'] for u in valid)
                outgoing += sum(u['output_tokens'] for u in valid)
                caches = [u['cached_input_tokens'] for u in valid if u['cached_input_tokens'] is not None]
                cached += sum(caches); measured_cache += len(caches)
            failed += c['state'] == 'failed'
            unfinished += c['state'] == 'unfinished'
            demo += c['demo']
            duration = max(0, c['end'] - c['start']) if c['end'] is not None and c['start'] is not None else None
            if duration is not None:
                elapsed += duration
            details.append({'call_id': c['call_id'], 'role': c['role'], 'group': group,
                'state': c['state'], 'demo': c['demo'], 'duration_seconds': duration,
                'reported_turns': len(valid), 'unmeasured_turns': unknown,
                'known_total_tokens': sum(u['total_tokens'] for u in valid) if valid else None,
                'evidence_ids': c['evidence_ids']})
        groups[group] = {'label': label, 'calls': len(members), 'calls_with_usage': measured,
            'calls_without_usage': len(members) - measured, 'unmeasured_turns': unmeasured_turns,
            'failed_calls': failed, 'unfinished_calls': unfinished, 'demo_calls': demo,
            'known_input_tokens': incoming if measured or not members else None,
            'known_cached_input_tokens': cached if measured_cache or not members else None,
            'known_output_tokens': outgoing if measured or not members else None,
            'known_total_tokens': incoming + outgoing if measured or not members else None,
            'usage_complete': not bool(unmeasured_turns),
            'sum_completed_call_seconds': round(elapsed, 3)}
    return {'schema_version': 1, 'groups': groups, 'calls': details,
            'cost_amount': None, 'currency': None, 'ability_score': None,
            'notes': ['사용량 누락은 0이 아닙니다. known_*는 보고된 부분 합계입니다.',
                      '캐시 입력은 입력 토큰의 부분집합이며 총계에 다시 더하지 않습니다.',
                      '환경 운영 비용을 응시자의 개발 비용에 섞지 않습니다.',
                      '시간 합계는 병렬 호출 시간을 합친 값이지 실제 경과시간이 아닙니다.',
                      '실제 청구액/구독 차감량을 추정하지 않습니다. 토큰 절약만으로 가점하지 않습니다.',
                      '현재 회차의 실행기를 통과한 호출만 집계합니다.']}
