"""Predict first, execute frozen submission second, reflect third.
Inputs are authored with the pack. Never execute model-supplied commands.
"""
from __future__ import annotations
import json
import sys
import uuid
from .fs import copy_workspace, safe_path
from .storage import write_json, canonical


def probes(problem):
    rel=problem.manifest.get('interview_probes') if problem else None
    if not rel:return []
    path=safe_path(problem.path,rel)
    data=json.loads(path.read_text(encoding='utf-8'))
    items=data.get('probes')
    if not isinstance(items,list):raise ValueError('면담 probe 목록 오류')
    ids=[]
    for p in items:
        if set(p)!={'id','question','input','args'} or not isinstance(p['id'],str) or not p['id'].strip() or not isinstance(p['question'],str) or not isinstance(p['input'],dict):
            raise ValueError('면담 probe 형식 오류')
        if not isinstance(p['args'],list) or not all(isinstance(x,str) for x in p['args']):raise ValueError('probe args 오류')
        if any(x in {'--input','--json'} for x in p['args']):raise ValueError('probe에서 입력 경로 옵션을 덮어쓸 수 없습니다.')
        ids.append(p['id'])
    if len(ids)!=len(set(ids)):raise ValueError('probe ID 중복')
    return items


def next_probe(exam,bundle):
    if not exam.config.get('interview_prediction'):return None
    count=exam.session().get('interview',{}).get('answers',0)
    if count not in {1,4}:return None
    choices=probes(exam.problem());used=exam.store.get('prediction_used',[])
    p=next((x for x in choices if x['id'] not in used),None)
    if not p:return None
    sources=[a['id'] for a in bundle['atoms'] if a.get('kind')=='source' and a.get('path')=='app.py']
    if not sources:sources=[a['id'] for a in bundle['atoms'] if a.get('kind')=='source'][:1]
    if not sources:return None
    exam.store.set('prediction_used',used+[p['id']])
    text='[실행 전 예측] '+p['question']+'\n제출한 app.py/어댑터를 이 입력으로 실행했을 때의 출력과 종료 상태를 먼저 설명하세요. 정답 규칙이 아니라 본인 제출 코드의 실제 동작을 예측하는 질문입니다.\n입력:\n'+json.dumps(p['input'],ensure_ascii=False,indent=2)
    if p['args']:text+='\n추가 옵션: '+canonical(p['args'])
    return {'question':text,'axis':'cognition','evidence_ids':sources,'kind':'prediction','probe_id':p['id']}


def execute_and_reflect(exam,question,deadline):
    p=next((x for x in probes(exam.problem()) if x['id']==question['probe_id']),None)
    if not p:raise RuntimeError('고정된 면담 probe를 찾지 못했습니다.')
    frozen=exam.verify_submission()
    directory=exam.root/'runtime'/('prediction-'+uuid.uuid4().hex[:10])
    copy_workspace(frozen/'workspace',directory/'workspace')
    path=directory/'input.json';write_json(path,p['input'])
    runner=exam.problem().manifest['runner']
    argv=[v.replace('{python}',sys.executable).replace('{workspace}',str(directory/'workspace')) for v in runner['argv']]
    argv+=['--input',str(path),'--json']+p['args']
    result=exam.run_command(argv,directory/'workspace',name='제출본 실행 예측 '+p['id'],scope='interview_probe',
                            visibility='internal',deadline=deadline,timeout_seconds=min(15,runner.get('case_timeout_seconds',5)))
    exam.verify_submission()
    from .evaluation import redact
    stdout=redact(result['stdout'])[0];stderr=redact(result['stderr'])[0]
    public={'probe_id':p['id'],'returncode':result['returncode'],'timed_out':result['timed_out'],
            'stdout':stdout[:12000],'stderr':stderr[:4000],
            'display_truncated':len(stdout)>12000 or len(stderr)>4000,
            'note':'본인 제출본의 실제 실행 결과. 참조 정답이나 수정안이 아닙니다.'}
    ident=exam.store.event('prediction_execution','interview',**public)
    history=exam.store.get('prediction_results',[]);history.append({**public,'evidence_id':ident,'run':result['run']});exam.store.set('prediction_results',history)
    q='[실행 후 대조] 아래는 고정된 제출본의 실행 결과입니다. 예측과 어떤 점이 같거나 다른지, 어느 코드 경로가 이유인지 설명하세요. 실행 실패라면 확인된 사실과 아직 모르는 원인을 구별하세요.\n'+json.dumps(public,ensure_ascii=False,indent=2)
    # Keep new runtime evidence in subsequent interview contexts. The final evaluator rebuilds all evidence.
    bundle=exam.store.get('interview_evidence')
    bundle['atoms'].append({'id':ident,'kind':'event','event_kind':'prediction_execution','channel':'interview','actor':'tool','content':canonical(public)})
    exam.store.set('interview_evidence',bundle)
    return {'question':q,'axis':'cognition','evidence_ids':question['evidence_ids']+[ident],
            'kind':'prediction_reflection','probe_id':p['id']}
