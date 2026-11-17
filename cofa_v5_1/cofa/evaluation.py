"""All-evidence chunk mapping, grounded interview context and three-axis grading.
No silent tail truncation: every eligible event/file atom is mapped or grading fails.
"""
from __future__ import annotations
import html
import json
import math
from pathlib import Path
import re
import time
from .storage import canonical, sha, write_json
from .fs import inventory
from .rubric import RUBRIC, IDS, ANCHORS, TOP_ANCHORS


def parse_json(text: str):
    text = text.strip()
    if text.startswith('```') and text.endswith('```'):
        text = '\n'.join(text.splitlines()[1:-1])
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError('LLM 응답은 JSON 객체여야 합니다.')
    return value


def validate_refs(refs, allowed, *, allow_empty=True):
    if not isinstance(refs, list) or not all(isinstance(r, str) for r in refs):
        raise ValueError('evidence_ids 형식 오류')
    if not allow_empty and not refs:
        raise ValueError('근거 없는 판단/질문을 거절했습니다.')
    unknown = set(refs) - set(allowed)
    if unknown:
        raise ValueError('존재하지 않는 증거 ID: ' + ', '.join(sorted(unknown)))


def redact(text: str):
    """Conservative common-token masking for evaluation payloads, not raw local logs.
    This is not a comprehensive secret detector.
    """
    patterns = [r'\bsk-[A-Za-z0-9_-]{20,}', r'(?i)\bBearer\s+[A-Za-z0-9_.-]{20,}',
                r'\bgh[pousr]_[A-Za-z0-9]{20,}', r'\bAKIA[A-Z0-9]{16}\b']
    count = 0
    for pattern in patterns:
        text, n = re.subn(pattern, '[REDACTED_TOKEN]', text)
        count += n
    return text, count


def evidence_group(atom):
    """Role in the actual recorded work, not length or an evaluator's praise."""
    if atom.get('channel') == 'interview' and atom.get('actor') == 'candidate':
        return 'candidate_answer'
    if atom.get('event_kind') in {'command_result', 'prediction_execution'} or (
        atom.get('event_kind') == 'codex_event' and 'command_execution' in atom.get('content', '')):
        return 'execution'
    if atom.get('kind') == 'source':
        return 'source'
    if atom.get('actor') == 'candidate':
        return 'candidate_decision'
    if atom.get('channel') in {'stakeholder', 'requirements'}:
        return 'business_dialogue'
    if atom.get('kind') in {'problem_reference', 'telemetry', 'binary_metadata'}:
        return 'reference'
    return 'other'


def pack_context(bundle, findings, max_chars=70000, *, focus=None):
    """Reserve raw evidence by role; never silently drop mapped findings.

    A partial excerpt is an exact substring with explicit offsets, not a new
    atom or a fabricated complete source. Full originals remain in the bundle
    and are retrieved by the per-criterion reviewer. Quotas are a practice
    design, not a claim about COFA's hidden evaluation algorithm.
    """
    atoms = {a['id']: a for a in bundle['atoms']}
    orders = {
        'technique': ('execution', 'source', 'candidate_decision', 'candidate_answer', 'business_dialogue'),
        'intent': ('candidate_decision', 'business_dialogue', 'execution', 'source', 'candidate_answer'),
        'cognition': ('candidate_answer', 'source', 'execution', 'candidate_decision', 'business_dialogue'),
    }
    order = orders.get(focus, ('candidate_answer', 'candidate_decision', 'execution', 'source', 'business_dialogue'))
    cited = list(dict.fromkeys(ref for f in findings for ref in f['evidence_ids']))
    if set(cited) - atoms.keys():
        raise ValueError('문맥 구성 중 존재하지 않는 근거 ID를 발견했습니다.')
    contrary = list(dict.fromkeys(ref for f in findings if f.get('polarity') in {'negative', 'uncertain'} for ref in f['evidence_ids']))
    ranked = contrary + [x for x in cited if x not in contrary] + [x for x in atoms if x not in cited]
    groups = {g: [x for x in ranked if evidence_group(atoms[x]) == g] for g in (*order, 'other', 'reference')}
    reserves = list(dict.fromkeys([groups[g][0] for g in order if groups[g]] + contrary[:1]))
    selected = list(dict.fromkeys(cited + reserves))
    if not selected:
        selected = list(atoms)[:6]
    raw = {}
    policy = {'focus': focus or 'balanced', 'rule': 'role_reserved_then_round_robin',
              'required_groups_present_in_bundle': [g for g in order if groups[g]],
              'represented_groups': [], 'missing_raw_groups': [], 'partial_evidence_ids': [],
              'contrary_without_raw': []}
    data = {'coverage': bundle.get('coverage', {}), 'findings': findings,
            'raw_excerpts': [], 'summary_only_evidence_ids': selected[:], 'raw_selection': policy,
            'note': '전체 원문은 청크 분석 대상입니다. 최종 단계는 모든 관찰 요약과 역할별 원문 발췌를 봅니다. 부분 발췌의 offset과 누락 종류를 명시합니다.'}

    def assemble():
        represented = list(dict.fromkeys(evidence_group(x) for x in raw.values()))
        data['raw_excerpts'] = list(raw.values())
        data['summary_only_evidence_ids'] = [x for x in selected if x not in raw]
        policy['represented_groups'] = represented
        policy['missing_raw_groups'] = [g for g in policy['required_groups_present_in_bundle'] if g not in represented]
        policy['partial_evidence_ids'] = [x for x, a in raw.items() if a.get('excerpt_partial')]
        policy['contrary_without_raw'] = [x for x in contrary if x not in raw]
        return canonical(data)

    if len(assemble()) > max_chars:
        raise RuntimeError('요약 근거가 문맥 한도를 초과했습니다. 내용을 버리지 않았습니다. 문맥 한도를 늘리세요.')

    def excerpt(ref, count):
        atom = atoms[ref]
        content = atom['content']
        # For JSON event records prefer the actual answer/output over metadata.
        start = 0
        if count < len(content) and atom.get('kind') == 'event':
            fields = ('"text":', '"stdout":') if evidence_group(atom) != 'execution' else ('"stdout":', '"text":')
            for field in fields:
                pos = content.find(field)
                if pos >= 0:
                    start = max(0, pos - 24)
                    break
            start = min(start, max(0, len(content) - count))
        end = min(len(content), start + count)
        return {**atom, 'content': content[start:end], 'excerpt_start': start, 'excerpt_end': end,
                'full_content_characters': len(content), 'excerpt_partial': start != 0 or end != len(content)}

    def put(ref, count, ceiling=max_chars):
        previous = raw.get(ref)
        raw[ref] = excerpt(ref, count)
        if len(assemble()) <= ceiling:
            return True
        if previous is None:
            del raw[ref]
        else:
            raw[ref] = previous
        assemble()
        return False

    # Minimum reserved views prevent a long code file from displacing an answer.
    for i, ref in enumerate(reserves):
        remaining = max_chars - len(assemble())
        per_slot = remaining // max(1, len(reserves) - i)
        overhead = len(canonical(excerpt(ref, 0))) + 100
        count = min(len(atoms[ref]['content']), max(0, per_slot - overhead))
        if count >= min(96, len(atoms[ref]['content'])):
            put(ref, count)
    # Expand priorities only after every available role has had a reservation.
    pending = [x for x in selected if x not in raw]
    queue = []
    pools = {g: [x for x in pending if evidence_group(atoms[x]) == g] for g in (*order, 'other', 'reference')}
    while any(pools.values()):
        for g in pools:
            if pools[g]:
                queue.append(pools[g].pop(0))
    for ref in queue + list(raw):
        full = len(atoms[ref]['content'])
        if put(ref, full):
            continue
        existing = len(raw[ref]['content']) if ref in raw else 0
        lo, hi = max(96, existing), full
        best = existing
        # Find a longest exact substring that fits, rather than omitting a large atom.
        while lo <= hi:
            mid = (lo + hi) // 2
            if put(ref, mid):
                best = mid; lo = mid + 1
            else:
                hi = mid - 1
        if best:
            put(ref, best)
    result = assemble()
    if len(result) > max_chars:
        raise RuntimeError('원문 문맥 배분이 제한을 초과했습니다.')
    return result


class Evidence:
    def __init__(self, exam):
        self.exam, self.root, self.store = exam, exam.root, exam.store
        self.directory = self.root / 'analysis'
        self.directory.mkdir(exist_ok=True)

    def build(self):
        frozen = self.exam.verify_submission()
        integrity = self.store.verify()
        if not integrity['ok']:
            raise RuntimeError('로그 무결성 검사 실패. 점수를 만들지 않았습니다.')
        atoms = []
        redactions = 0
        events = self.store.events()
        eligible = []
        finished = [e['seq'] for e in events if e['kind'] == 'interview_finish']
        cutoff = finished[-1] if finished else None
        for e in events:
            if cutoff is not None and e['seq'] > cutoff:
                continue
            role = e['channel']
            if role in {'feedback','coaching'} or e['kind'] in {'feedback_complete','feedback_error','evaluation_complete','evaluation_progress'}:
                continue
            is_development_call = role in {'main', 'auditor'} or role.startswith('worker:')
            include = e['kind'] in {'stakeholder_decision', 'stakeholder_invalid_output', 'stakeholder_reconsideration', 'requirements_review'} or (e['kind'] == 'command_result' and e['data'].get('scope') in {'problem_hidden','interview_probe'}) or e['visibility'] == 'candidate' or (
                is_development_call and e['kind'] in {'llm_complete', 'codex_stderr'})
            if not include or e['kind'] in {'evaluation_progress', 'evaluation_complete'}:
                continue
            eligible.append(e)
            text, count = redact(canonical({'kind': e['kind'], 'channel': role, 'at': e['at'], **e['data']}))
            redactions += count
            parts = [text[i:i + 7000] for i in range(0, len(text), 7000)] or ['']
            for n, content in enumerate(parts):
                atoms.append({'id': e['id'] if len(parts) == 1 else f'{e["id"]}:p{n + 1}',
                              'kind': 'event', 'event_kind': e['kind'], 'channel': role,
                              'actor': e['data'].get('actor', 'tool' if e['kind'] in {'command_result', 'codex_event'} else 'system'),
                              'source_event': e['id'], 'part': n + 1, 'parts': len(parts), 'content': content})
        files, exclusions = inventory(frozen / 'workspace')
        text_files, binary_files = 0, 0
        for number, (rel, digest) in enumerate(files.items(), 1):
            path = frozen / 'workspace' / rel
            raw = path.read_bytes()
            try:
                text = raw.decode('utf-8-sig')
                if '\x00' in text:
                    raise UnicodeError('binary')
            except UnicodeError:
                binary_files += 1
                atoms.append({'id': f'F{number:05d}:binary', 'kind': 'binary_metadata', 'path': rel,
                              'content': canonical({'sha256': digest, 'bytes': len(raw), 'inspected': False})})
                continue
            text_files += 1
            text, count = redact(text)
            redactions += count
            # Preserve line numbers, including exceptionally long individual lines.
            numbered = '\n'.join(f'{i}: {line}' for i, line in enumerate(text.splitlines(), 1))
            parts = [numbered[i:i + 7000] for i in range(0, len(numbered), 7000)] or ['[empty file]']
            for n, content in enumerate(parts, 1):
                atoms.append({'id': f'F{number:05d}:p{n}', 'kind': 'source', 'path': rel,
                              'sha256': digest, 'part': n, 'parts': len(parts), 'content': content})
        from .metrics import usage_report
        usage = usage_report(self.store)
        telemetry = canonical({'development': usage['groups']['development'],
                               'calls': [c for c in usage['calls'] if c['group'] == 'development'],
                               'notes': usage['notes'], 'ability_score': None})
        usage_parts = [telemetry[i:i+7000] for i in range(0, len(telemetry), 7000)]
        for n, content in enumerate(usage_parts, 1):
            atoms.append({'id': f'USAGE:development:p{n}', 'kind': 'telemetry', 'actor': 'tool',
                          'channel': 'resources', 'part': n, 'parts': len(usage_parts), 'content': content})
        problem = self.exam.problem()
        if problem:
            reference = canonical(problem.assessment_reference())
            parts = [reference[i:i+7000] for i in range(0, len(reference), 7000)]
            for n, part in enumerate(parts, 1):
                atoms.append({'id':f'PREF:p{n}', 'kind':'problem_reference', 'actor':'reference',
                              'path':'AUTHORITATIVE_BUSINESS_CONTRACT_NOT_CANDIDATE_WORK',
                              'part':n, 'parts':len(parts), 'content':part})
        coverage = {'stored_event_count': len(events), 'eligible_event_count': len(eligible),
                    'excluded_internal_event_count': len(events) - len(eligible),
                    'artifact_text_files': text_files, 'binary_metadata_only_files': binary_files,
                    'excluded_workspace_paths': exclusions, 'atom_count': len(atoms),
                    'redacted_token_matches': redactions, 'event_head_sha256': integrity['head'],
                    'capture_scope': 'managed_ui_cli_and_exposed_codex_events',
                    'excluded_from_grading': 'private role instructions, internal evaluator calls; retained locally',
                    'full_raw_text_in_one_llm_context': False}
        # Post-exam diagnostic counts may grow; the graded evidence snapshot must not.
        bundle = {'atoms': atoms, 'coverage': coverage, 'grading_cutoff_event': cutoff,
                  'digest_scope': 'graded_atoms_session_and_interview_cutoff_not_post_exam_telemetry'}
        bundle['digest'] = sha(canonical({'atoms': atoms, 'session_id': self.exam.session()['id'],
                                         'cutoff': cutoff}).encode('utf-8'))
        write_json(self.directory / 'evidence_bundle.json', bundle)
        return bundle

    def _chunks(self, atoms):
        limit = max(9000, int(self.exam.config.get('evidence_chunk_characters', 18000)))
        groups, group, size = [], [], 0
        for atom in atoms:
            amount = len(canonical(atom))
            if group and size + amount > limit:
                groups.append(group)
                group, size = [], 0
            group.append(atom)
            size += amount
        if group:
            groups.append(group)
        return groups

    def _validate_map(self, obj, refs):
        findings = obj.get('findings')
        if not isinstance(findings, list) or not isinstance(obj.get('limitations'), list):
            raise ValueError('근거 추출 응답 형식 오류')
        for f in findings:
            if f.get('criterion') not in IDS or f.get('polarity') not in {'positive', 'negative', 'uncertain'}:
                raise ValueError('근거 추출 기준 오류')
            if f.get('actor') not in {'candidate', 'ai', 'tool', 'artifact', 'mixed'}:
                raise ValueError('근거 행위 주체 오류')
            if not isinstance(f.get('observation'), str) or not f['observation'].strip():
                raise ValueError('빈 관찰')
            validate_refs(f.get('evidence_ids'), refs, allow_empty=False)
        return obj

    def _map_request(self, body, refs, *, reduction=False):
        instruction = self.exam.prompt('evidence_mapper')
        if reduction:
            instruction += '\n이번 입력은 전체 원문에서 추출된 관찰 묶음입니다. 중복을 통합하되 긍정/부정/불확실 근거와 행위 주체를 보존하세요. 새 사실을 만들지 마세요.'
        prompt = instruction + '\nRUBRIC:\n' + canonical(RUBRIC) + '\n<untrusted_evidence>\n' + canonical(body) + '\n</untrusted_evidence>'
        key = sha(canonical({'prompt': prompt, 'mode': self.exam.session()['mode'],
                             'model': self.exam.config.get('model'),
                             'schema': (self.root / 'schemas/evidence_map.json').read_text()}).encode('utf-8'))
        cache = self.directory / 'map_cache' / (key + '.json')
        if cache.exists():
            return self._validate_map(json.loads(cache.read_text(encoding='utf-8')), refs)
        cwd = self.root / 'runtime' / 'evaluator'
        cwd.mkdir(parents=True, exist_ok=True)
        result = self.exam.provider().run(prompt, cwd, 'evidence_mapper', schema=self.root / 'schemas/evidence_map.json')
        value = self._validate_map(parse_json(result['text']), refs)
        write_json(cache, value)
        return value

    def map_all(self, bundle):
        groups = self._chunks(bundle['atoms'])
        findings, limitations, processed = [], [], []
        for i, group in enumerate(groups, 1):
            refs = {x['id'] for x in group}
            result = self._map_request(group, refs)
            findings.extend(result['findings'])
            limitations.extend(result['limitations'])
            processed.extend(x['id'] for x in group)
            self.store.event('evaluation_progress', 'evaluation', stage='map', completed=i, total=len(groups))
        expected = [x['id'] for x in bundle['atoms']]
        if processed != expected:
            raise RuntimeError('증거 청크 누락/중복. 채점을 중단합니다.')
        coverage = {**bundle['coverage'], 'mapped_atoms': len(processed), 'total_atoms': len(expected),
                    'map_chunks': len(groups), 'all_eligible_atoms_mapped': True,
                    'processed_atom_ids': processed, 'limitations': limitations}
        write_json(self.directory / 'coverage.json', coverage)
        write_json(self.directory / 'all_findings.json', findings)
        # All original findings remain saved; downstream context is hierarchically reduced.
        cap = int(self.exam.config.get('evaluation_max_context_characters', 70000))
        all_refs = set(expected)
        reduced = findings
        levels = 0
        while len(canonical(reduced)) > cap // 2:
            if levels >= 4:
                raise RuntimeError('4단계 요약 후에도 근거가 너무 큽니다. 중도 잘라내지 않았습니다. 문맥 한도를 늘려 재시도하세요.')
            groups2, group, size = [], [], 0
            for f in reduced:
                length = len(canonical(f))
                if group and size + length > 16000:
                    groups2.append(group)
                    group, size = [], 0
                group.append(f)
                size += length
            if group:
                groups2.append(group)
            next_level = []
            for g in groups2:
                # A reducer may only cite IDs that were present in its own input.
                local_refs = {x for f in g for x in f['evidence_ids']}
                next_level.extend(self._map_request(g, local_refs, reduction=True)['findings'])
            reduced = next_level
            levels += 1
        coverage['reduction_levels'] = levels
        write_json(self.directory / 'coverage.json', coverage)
        write_json(self.directory / 'reduced_findings.json', reduced)
        return reduced


class Evaluator:
    def __init__(self, exam):
        self.exam, self.root, self.store = exam, exam.root, exam.store

    def validate_axis(self, value, axis, bundle):
        expected = set(RUBRIC[axis]['criteria'])
        criteria = value.get('criteria')
        if value.get('axis') != axis or not isinstance(criteria, list) or len(criteria) != 3:
            raise ValueError('평가축/하위항목 형식 오류')
        if {x.get('id') for x in criteria} != expected:
            raise ValueError('하위항목 누락 또는 중복')
        refs = {a['id']: a for a in bundle['atoms']}
        for c in criteria:
            rating, status = c.get('rating'), c.get('status')
            if status not in {'observed', 'insufficient_evidence'}:
                raise ValueError('채점 상태 오류')
            if rating is not None and (type(rating) is not int or not 0 <= rating <= 4):
                raise ValueError('점수는 0~4 정수 또는 null이어야 합니다.')
            if (rating is None) != (status == 'insufficient_evidence'):
                raise ValueError('관찰 상태와 점수가 불일치합니다.')
            validate_refs(c.get('evidence_ids'), refs, allow_empty=rating is None)
            if not isinstance(c.get('reason'), str) or not c['reason'].strip():
                raise ValueError('판단 근거 설명 누락')
            conf = c.get('confidence')
            if type(conf) not in (int, float) or not math.isfinite(conf) or not 0 <= conf <= 1:
                raise ValueError('confidence는 0~1의 유한 수여야 합니다.')
            if rating is not None and all(refs[r].get('kind') in {'problem_reference', 'telemetry'} for r in c['evidence_ids']):
                c.update(rating=None, status='insufficient_evidence', confidence=0,
                         reason=c['reason'] + ' [정답 명세나 사용량 수치만으로 응시자 수행을 증명할 수 없음.]')
                rating = None
            if axis == 'cognition' and rating is not None:
                candidate_answer = any(refs[r].get('channel') == 'interview' and
                                       refs[r].get('actor') == 'candidate' for r in c['evidence_ids'])
                if not candidate_answer:
                    c.update(rating=None, status='insufficient_evidence', confidence=0,
                             reason=c['reason'] + ' [검증기: 응시자 면담 답변의 직접 근거가 없어 점수를 보류함.]')
        return value

    def run(self):
        frozen = self.exam.verify_submission()
        evidence = Evidence(self.exam)
        bundle = evidence.build()
        findings = evidence.map_all(bundle)
        report_dir = self.root / 'reports'
        report_dir.mkdir(exist_ok=True)
        grades = {}
        for axis in RUBRIC:
            # All relevant mapped findings are provided, never just the last N messages.
            relevant = [f for f in findings if f['criterion'] in RUBRIC[axis]['criteria']]
            context = pack_context(bundle, relevant, self.exam.config['evaluation_max_context_characters'], focus=axis)
            payload = self.exam.prompt('judge') + '\nAXIS=' + axis + '\n'
            payload += '평가기준:\n' + canonical(RUBRIC[axis]) + '\n앵커:\n' + canonical(ANCHORS) + '\n항목별 최고 수준 앵커(실패 이력 필수 아님):\n' + canonical(TOP_ANCHORS)
            payload += '\n<untrusted_evidence>\n' + context + '\n</untrusted_evidence>'
            result = self.exam.provider().run(payload, self.root / 'runtime' / 'evaluator', 'judge:' + axis,
                                             schema=self.root / 'schemas/axis_score.json')
            value = self.validate_axis(parse_json(result['text']), axis, bundle)
            if self.exam.session()['mode'] == 'demo' and any(c['rating'] is not None for c in value['criteria']):
                raise ValueError('DEMO에서 실제 능력 점수를 만들 수 없습니다.')
            alternate = None
            if self.exam.config.get('judge_repetitions', 1) >= 2 and self.exam.session()['mode'] != 'demo':
                again = self.exam.provider().run(payload, self.root / 'runtime' / 'evaluator', 'judge:repeat:' + axis,
                                                schema=self.root / 'schemas/axis_score.json')
                alternate = self.validate_axis(parse_json(again['text']), axis, bundle)
                write_json(report_dir / ('axis_' + axis + '_repeat.json'), alternate)
            if self.exam.config.get('semantic_evidence_review'):
                from .quality import review_axis
                value = review_axis(self.exam, value, bundle, findings, alternate)
            value['score_100'] = round(sum(c['rating'] for c in value['criteria']) / 12 * 100, 2) if all(
                c['rating'] is not None for c in value['criteria']) else None
            write_json(report_dir / ('axis_' + axis + '.json'), value)
            write_json(report_dir / ('context_' + axis + '.json'), json.loads(context))
            grades[axis] = value
        self.exam.verify_submission()
        all_scores = [x['score_100'] for x in grades.values()]
        total = round(sum(all_scores) / 3, 2) if all(x is not None for x in all_scores) else None
        coverage = json.loads((self.root / 'analysis/coverage.json').read_text(encoding='utf-8'))
        submission = json.loads((frozen / 'submission.json').read_text(encoding='utf-8'))
        value = {'title': 'COFA-style 비공식 훈련 평가', 'created_at': time.time(),
                 'session_id': self.exam.session()['id'], 'evidence_digest':bundle['digest'],
                 'official_score': False, 'weights': {'technique': 1/3, 'intent': 1/3, 'cognition': 1/3},
                 'weight_source': '사용자 이미지의 3축/9기준. 0~4 및 동등 가중치는 모의환경 설계.',
                 'mode': self.exam.session()['mode'], 'axes': grades, 'total_100': total,
                 'score_status': 'demo_no_score' if self.exam.session()['mode'] == 'demo' else (
                     'complete' if total is not None else 'partially_unresolved'),
                 'coverage': coverage, 'interview': self.exam.session().get('interview'),
                 'submission_on_time': submission['within_time'],
                 'submission_timing': {key: submission.get(key) for key in (
                     'timing_status', 'requested_at', 'frozen_at', 'capture_within_time', 'late_capture_seconds')},
                 'harness_changes': submission['harness_changes'],
                 'limitations': [
                     '실제 COFA 배점·정답·합격선과 무관한 연습용 점수입니다.',
                     '실행기 밖의 채팅/도구는 자동 수집하지 못합니다. 비공개 내부 추론은 수집하지 않습니다.',
                     'LLM 판단에는 오류·프롬프트 인젝션·동일 모델 편향이 남을 수 있습니다.',
                     '역할별 세션 분리는 통계적 독립 평가자 또는 OS 수준 격리를 뜻하지 않습니다.',
                     '전체 원문은 청크별 분석되며 최종 채점기는 전체 관찰 요약과 예산 내 원문 발췌를 사용합니다.',
                     'UI 조작·게임 엔진 실행·바이너리 내용은 별도 실행 근거 없이는 검증되지 않습니다.']}
        problem = self.exam.problem()
        if problem:
            value['problem'] = problem.summary()
            value['disclosed_requirement_ids'] = self.store.get('disclosed_facts', [])
            value['functional_tests'] = []
            for result in self.store.get('submission_validation', []):
                if result.get('scope') == 'problem_hidden':
                    try: value['functional_tests'].append(json.loads(result['stdout']))
                    except (ValueError, KeyError):
                        value['functional_tests'].append({'error':'검증 프로세스 실패','passed':False})
            value['functional_tests_note'] = '실행 정답 검사 결과입니다. 3축 역량 점수로 자동 변환하지 않습니다.'
        value['evidence_review'] = {'enabled': bool(self.exam.config.get('semantic_evidence_review')), 'repeated_judges': self.exam.config.get('judge_repetitions', 1), 'human_calibrated': False}
        value['resource_usage'] = self.exam.usage()
        value['preparation'] = self.exam.session().get('preparation', {'kits': []})
        value['reflection_profile'] = 'v5.1_stabilized_evidence_feedback_not_official_rubric'
        value['unresolved_criteria'] = [{k: c.get(k) for k in ('id', 'hold_reason', 'hold_reasons', 'review_status', 'reason')} for a in grades.values() for c in a['criteria'] if c['rating'] is None]
        write_json(report_dir / 'resources.json', value['resource_usage'])
        write_json(report_dir / 'final_report.json', value)
        self.render(value, report_dir)
        self.store.event('evaluation_complete', 'evaluation', total=total, report='reports/final_report.json')
        return value

    def render(self, report, path):
        score = lambda x: '미확정(항목별 사유 확인)' if x is None else f'{x:.2f}'
        lines = ['# COFA-style 비공식 훈련 평가', '',
                 '**공식 COFA 점수·합격 예측이 아닙니다.**', '',
                 f'- 실행 모드: {report["mode"]}', f'- 총점 /100: {score(report["total_100"])}',
                 '- 3축 동등 가중치. 근거 없는 기준은 0점 대신 보류.',
                 f'- 전체 평가 대상 근거: {report["coverage"]["mapped_atoms"]}/{report["coverage"]["total_atoms"]} 청크 원자 분석 완료',
                 f'- 기한 내 제출 요청: {report["submission_on_time"]}',
                 f'- 제출 방식/시각 상태: {report["submission_timing"]["timing_status"]}',
                 f'- 마감 후 실제 복사 완료 지연(초): {report["submission_timing"]["late_capture_seconds"]}', '']
        if report.get('problem'):
            lines += ['## 문제팩', report['problem']['title'], '', '실제 인수 검사:',
                      '```json', json.dumps(report.get('functional_tests'), ensure_ascii=False, indent=2), '```',
                      report['functional_tests_note'], '']
        if report.get('resource_usage'):
            lines += ['## 개발 사용량과 환경 운영 사용량',
                      '토큰 자체는 점수가 아닙니다. 미보고는 0이 아니며 실제 금액은 추정하지 않습니다.',
                      '```json', json.dumps(report['resource_usage']['groups'], ensure_ascii=False, indent=2), '```', '']
        if report.get('preparation', {}).get('kits'):
            lines += ['## 사전 반입 지침', '기존 자료의 출처와 선택 이유를 별도 기록합니다.',
                      '```json', json.dumps(report['preparation'], ensure_ascii=False, indent=2), '```', '']
        for axis, value in report['axes'].items():
            lines += [f'## {RUBRIC[axis]["name"]}: {score(value["score_100"])}/100', '']
            for c in value['criteria']:
                rating = '보류' if c['rating'] is None else str(c['rating'])
                lines += [f'### {c["id"]} {RUBRIC[axis]["criteria"][c["id"]]} — {rating}/4',
                          c['reason'], '', '근거: ' + (', '.join(c['evidence_ids']) or '없음'),
                          f'모델의 자기보고 확신도: {c["confidence"]:.2f} (교정된 확률 아님)', '']
        lines += ['## 채점 재검토', '반복 점수가 다르거나 인용이 판단을 지지하지 못하면 해당 항목을 보류합니다. 별도 LLM 호출은 인간 검증이나 통계적 독립을 의미하지 않습니다.', '', '## 한계', *['- ' + x for x in report['limitations']], '',
                  '근거 원문: analysis/evidence_bundle.json, 전체 관찰: analysis/all_findings.json',
                  '대화·도구 기록: logs/session.sqlite3 및 logs/events.jsonl, 호출 원본: logs/calls/.']
        for axis_value in report['axes'].values():
            for criterion in axis_value['criteria']:
                if 'semantic_review' in criterion or 'repeated_ratings' in criterion:
                    lines += ['### ' + criterion['id'] + ' 재검토', '```json', json.dumps({k:criterion[k] for k in ('draft_rating','repeated_ratings','review_status','hold_reason','hold_reasons','adjudication','semantic_review') if k in criterion},ensure_ascii=False,indent=2), '```']
        text = '\n'.join(lines)
        (path / 'final_report.md').write_text(text, encoding='utf-8')
        bundle = json.loads((self.root / 'analysis/evidence_bundle.json').read_text(encoding='utf-8'))
        atoms = {a['id']: a for a in bundle['atoms']}
        esc = html.escape
        cards, sections, cited = [], [], []
        for axis, value in report['axes'].items():
            cards.append('<div class="card"><span>' + RUBRIC[axis]['name'] + '</span><strong>' +
                         esc(score(value['score_100'])) + '</strong><small>/ 100</small></div>')
            rows = []
            for c in value['criteria']:
                links = []
                for ref in c['evidence_ids']:
                    links.append('<a href="#' + esc(ref, quote=True) + '">' + esc(ref) + '</a>')
                    if ref not in cited:
                        cited.append(ref)
                rating = '보류' if c['rating'] is None else str(c['rating']) + '/4'
                rows.append('<article><h3>' + c['id'] + ' · ' + esc(RUBRIC[axis]['criteria'][c['id']]) +
                            '<b>' + rating + '</b></h3><p>' + esc(c['reason']) + '</p><div class="refs">근거: ' +
                            (', '.join(links) or '없음') + '</div><small>모델 자기보고 확신도 ' +
                            str(c['confidence']) + ' · 교정된 확률 아님</small></article>')
            sections.append('<h2>' + RUBRIC[axis]['name'] + '</h2>' + ''.join(rows))
        sources = []
        for ref in cited:
            atom = atoms[ref]
            origin = atom.get('path') or (atom.get('channel', '') + ' · ' + atom.get('actor', ''))
            sources.append('<details id="' + esc(ref, quote=True) + '"><summary>' + esc(ref + ' · ' + origin) +
                           '</summary><pre>' + esc(atom['content']) + '</pre></details>')
        notice = 'DEMO: 실제 LLM 역량 점수 없음' if report['mode'] == 'demo' else '비공식 연습 평가 · 공식 COFA 점수/합격 예측 아님'
        css = """body{font:15px/1.65 system-ui;margin:0;background:#f3f6f3;color:#24362b}main{max-width:1050px;margin:auto;padding:36px 24px}header{background:#19372b;color:#fff;padding:30px;border-radius:14px}h1{margin:4px 0;font-size:30px}header small{color:#cde3d1}.cards{display:flex;gap:12px;margin:20px 0}.card{flex:1;background:#fff;border:1px solid #dae4dc;padding:18px;border-radius:10px}.card strong{display:block;font-size:25px}.card small,small{color:#647568}h2{margin-top:32px}article{background:#fff;border:1px solid #d9e2dc;padding:18px;margin:12px 0;border-radius:9px}h3{font-size:16px;margin:0}h3 b{float:right}p{white-space:pre-wrap}.refs{font-size:12px}a{color:#486b17}details{background:#fff;border:1px solid #d9e2dc;padding:12px;margin:8px 0;border-radius:8px}summary{cursor:pointer}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:12px/1.6 ui-monospace,monospace}.meta{font-size:13px;color:#526457}.notice{padding:13px;background:#e7efdb;border-radius:8px}@media(max-width:650px){.cards{display:block}.card{margin:8px 0}h3 b{float:none;margin-left:10px}}"""
        page = '<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>COFA-style 훈련 평가</title><style>' + css + '</style><main><header><small>TRAINING EVIDENCE REPORT</small><h1>COFA-style 평가 리포트</h1><div>' + notice + '</div></header><p class="notice">총점 /100: <b>' + esc(score(report['total_100'])) + '</b> · 근거 부족은 0점이 아니라 보류합니다.</p><div class="cards">' + ''.join(cards) + '</div><p class="meta">평가 대상 원문 분석: ' + str(report['coverage']['mapped_atoms']) + '/' + str(report['coverage']['total_atoms']) + ' 원자. 최종 채점은 전체 관찰 요약과 명시된 원문 발췌를 사용했습니다.</p>' + ''.join(sections) + '<h2>판단에 인용된 실제 근거</h2>' + (''.join(sources) or '<p>인용할 평가 근거가 없습니다. DEMO에서는 역량을 평가하지 않습니다.</p>') + '<h2>한계와 주의</h2><ul>' + ''.join('<li>' + esc(x) + '</li>' for x in report['limitations']) + '</ul></main></html>'
        if report.get('problem'):
            functional = '<h2>문제팩 · 실행 정답 검사</h2><p>' + esc(report['problem']['title']) + '</p><pre>' + esc(json.dumps(report.get('functional_tests'), ensure_ascii=False, indent=2)) + '</pre><p>' + esc(report['functional_tests_note']) + '</p>'
            page = page.replace('<h2>판단에 인용된 실제 근거</h2>',functional + '<h2>판단에 인용된 실제 근거</h2>')
        resources = '<h2>개발 / 환경 운영 사용량</h2><p>토큰 절약 자체는 점수가 아닙니다. 미보고는 0이 아니며, 금액을 추정하지 않습니다.</p><pre>' + esc(json.dumps(report.get('resource_usage', {}).get('groups', {}), ensure_ascii=False, indent=2)) + '</pre>'
        if report.get('preparation', {}).get('kits'):
            resources += '<h2>사전 반입 지침</h2><pre>' + esc(json.dumps(report['preparation'], ensure_ascii=False, indent=2)) + '</pre>'
        page = page.replace('<h2>한계와 주의</h2>', resources + '<h2>한계와 주의</h2>')
        audit = '<h2>채점 재검토 · 피드백</h2><p>반복 불일치/지원되지 않는 판단은 보류합니다. 별도 LLM 호출은 사람 검증이 아닙니다.</p><p><a href="feedback.html">시험 종료 피드백 열기</a></p><pre>' + esc(json.dumps({a:[{k:c[k] for k in ('id','draft_rating','repeated_ratings','review_status','hold_reason','hold_reasons','adjudication','semantic_review') if k in c} for c in v['criteria']] for a,v in report['axes'].items()},ensure_ascii=False,indent=2)) + '</pre>'
        page = page.replace('<h2>한계와 주의</h2>', audit+'<h2>한계와 주의</h2>')
        (path / 'final_report.html').write_text(page, encoding='utf-8')
