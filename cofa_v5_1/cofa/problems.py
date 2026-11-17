"""Versioned problem packs: the only content-specific connection to the exam engine.
Declarative validation never executes an imported pack. Cases execute programs only when requested.
"""
from __future__ import annotations
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile
import uuid
from .fs import inventory, copy_workspace, safe_path
from .storage import canonical, sha, write_json

ID = re.compile(r'^[a-z][a-z0-9-]{2,63}$')


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


class Problem:
    def __init__(self, root: Path, problem_id: str):
        if not isinstance(problem_id, str) or not ID.fullmatch(problem_id):
            raise ValueError('문제 ID는 소문자 영문 시작, 소문자/숫자/하이픈 3~64자입니다.')
        self.root = root.resolve()
        self.path = safe_path(self.root / 'problem_packs', problem_id)
        self.manifest = read_json(self.path / 'problem.json')
        if self.manifest.get('id') != problem_id: raise ValueError('폴더 ID와 manifest id 불일치')

    def file(self, key):
        return safe_path(self.path, self.manifest[key])

    def truth(self):
        return read_json(self.file('truth'))

    def fingerprint(self):
        files, excluded = inventory(self.path)
        return sha(canonical(files).encode('utf-8'))

    def summary(self):
        m = self.manifest
        return {k:m.get(k) for k in ('id','version','title','track','ready','description','stakeholder_name')}

    def validate(self, allow_draft=False):
        m = self.manifest
        if m.get('schema_version') != 1: raise ValueError('지원하지 않는 문제팩 버전')
        if not m.get('ready') and not allow_draft: raise ValueError('초안 문제팩입니다. 정답/테스트 작성 후 ready=true로 변경하세요.')
        for field in ('title','version','track','stakeholder_name'):
            if not isinstance(m.get(field),str) or not m[field]: raise ValueError('manifest 누락: '+field)
        # Reject links rather than silently ignoring a private answer escape.
        for p in self.path.rglob('*'):
            if p.is_symlink(): raise ValueError('문제팩 심볼릭 링크 금지: '+str(p))
        for key in ('public_dir','reference_dir'):
            if not self.file(key).is_dir(): raise ValueError('폴더 없음: '+key)
        for key in ('truth','solution','interview_guide'):
            if not self.file(key).is_file(): raise ValueError('파일 없음: '+key)
        public = self.file('public_dir').resolve()
        for key in ('truth','solution','interview_guide','reference_dir'):
            if self.file(key).resolve().is_relative_to(public): raise ValueError('정답이 public 폴더에 있습니다.')
        if not (public/'MISSION.md').is_file() or not (public/'CONTRACT.md').is_file():
            raise ValueError('public/MISSION.md와 CONTRACT.md가 필요합니다.')
        truth = self.truth()
        from .stakeholder import disclosure_policy
        disclosure_policy(truth)
        facts = truth.get('facts')
        if not isinstance(facts,list) or not facts: raise ValueError('업무 사실 facts 필요')
        fact_ids=[]
        for f in facts:
            for field in ('id','topic','answer','visibility','kind'):
                if not isinstance(f.get(field),str) or not f[field]:raise ValueError('fact 필드 오류: '+field)
            if f['visibility'] not in ('public','overview','ask'):raise ValueError('공개 정책 오류')
            if not isinstance(f.get('keywords'),list) or not all(isinstance(x,str) for x in f['keywords']):raise ValueError('keywords 오류')
            fact_ids.append(f['id'])
        if len(set(fact_ids)) != len(fact_ids):raise ValueError('fact ID 중복')
        if not set(truth.get('disclosure',{}).get('overview_ids',[])).issubset(fact_ids):raise ValueError('overview ID 오류')
        if m.get('stakeholder_benchmark'):
            route_path=safe_path(self.path,m['stakeholder_benchmark'])
            if route_path.resolve().is_relative_to(public):raise ValueError('담당자 벤치마크는 private에 두세요.')
            route_data=read_json(route_path)
            if not isinstance(route_data.get('cases'),list):raise ValueError('담당자 벤치마크 cases 오류')
            for case in route_data['cases']:
                if not isinstance(case.get('question'),str) or not isinstance(case.get('expected_intents'),list):raise ValueError('담당자 벤치마크 질문 오류')
                if not set(case.get('must_include',[])+case.get('must_exclude',[])).issubset(fact_ids):raise ValueError('담당자 벤치마크 조항 오류')
        from .prediction import probes
        if m.get('interview_probes'):
            if safe_path(self.path,m['interview_probes']).resolve().is_relative_to(public): raise ValueError('면담 probe는 private에 두세요.')
            probes(self)
        counts={}; tested=set()
        for suite in ('public','private'):
            p=safe_path(self.path,m['suites'][suite]); data=read_json(p)
            if suite=='private' and p.resolve().is_relative_to(public):raise ValueError('비공개 테스트가 public에 있습니다.')
            if data.get('schema_version')!=1 or not isinstance(data.get('cases'),list) or not data['cases']:
                raise ValueError('비어 있거나 잘못된 테스트 모음')
            ids=[]
            for c in data['cases']:
                ids.append(c['id'])
                refs=c.get('requirement_ids')
                if not isinstance(refs,list) or not refs or not set(refs).issubset(fact_ids):raise ValueError('테스트에 알려지지 않은 요구 ID')
                if not isinstance(c.get('expected'),dict):raise ValueError('독립 expected 객체 필요')
                if type(c.get('expected_exit',0)) is not int:raise ValueError('expected_exit 정수 필요')
                if not isinstance(c.get('args',[]),list) or not all(isinstance(x,str) for x in c.get('args',[])):raise ValueError('args 배열 오류')
                if suite=='private':tested.update(refs)
            if len(set(ids))!=len(ids):raise ValueError('테스트 ID 중복')
            counts[suite]=len(ids)
        for item in m.get('checks',[]):
            if 'timeout_seconds' in item and (type(item['timeout_seconds']) not in (int,float) or not 0 < item['timeout_seconds'] <= 3600):raise ValueError('검증 timeout_seconds는 0 초과 3600 이하 숫자')
            if item.get('phase') not in ('development','submission','both'):raise ValueError('검증 phase 오류')
            if not isinstance(item.get('argv'),list) or not item['argv'] or not all(isinstance(x,str) for x in item['argv']):raise ValueError('검증 argv 오류')
        argv=m.get('runner',{}).get('argv')
        if not isinstance(argv,list) or not argv or not all(isinstance(x,str) for x in argv):raise ValueError('runner argv 오류')
        required={f['id'] for f in facts if f['kind']=='required'}
        return {**self.summary(),'valid':True,'digest':self.fingerprint(),'cases':counts,
                'automated_requirement_ids':sorted(tested),'manual_or_uncovered_requirement_ids':sorted(required-tested),
                'warning':'문제팩은 신뢰할 수 있는 출제자가 작성해야 합니다. 검증 명령은 로컬 코드를 실행합니다.'}

    def verify_pinned(self, session):
        pinned=session.get('problem')
        if pinned and (pinned['id']!=self.manifest['id'] or pinned['digest']!=self.fingerprint()):
            raise RuntimeError('시작 시 고정한 문제팩이 변경되었습니다. 담당자/면담/평가를 중단했습니다. 원래 문제팩을 복원하세요.')

    def assessment_reference(self):
        # Deliberately no reference source code: equivalent implementations are allowed.
        return {'problem':self.summary(),'business_truth':self.truth(),
                'solution_explanation':self.file('solution').read_text(encoding='utf-8'),
                'interview_guide':self.file('interview_guide').read_text(encoding='utf-8'),
                'note':'정답/계약 참고자료이지 응시자가 수행한 증거가 아님. 코드 동일성으로 채점하지 말 것.'}


def active_problem(exam):
    path=exam.root/'active_problem.json'
    state=exam.store.get('session',{})
    selection=state.get('problem') or (read_json(path) if path.exists() else None)
    if not selection:return None
    problem=Problem(exam.root,selection['id'])
    if state:problem.verify_pinned(state)
    return problem


def list_problems(root):
    result=[]
    for p in sorted((root/'problem_packs').glob('*/problem.json')):
        try:result.append(Problem(root,p.parent.name).summary())
        except Exception as exc:result.append({'id':p.parent.name,'error':str(exc)})
    return result


def select_problem(exam, problem_id):
    if exam.store.get('session'):raise RuntimeError('시작한 회차의 문제는 바꿀 수 없습니다. problem new-session으로 새 폴더를 만드세요.')
    problem=Problem(exam.root,problem_id); report=problem.validate()
    staging=exam.root/('workspace-select-'+uuid.uuid4().hex[:8])
    copy_workspace(problem.file('public_dir'),staging)
    backup=None
    if exam.workspace.exists():
        backup=exam.root/'backups'/('workspace-'+uuid.uuid4().hex[:8]);backup.parent.mkdir(parents=True,exist_ok=True)
        exam.workspace.rename(backup)
    staging.rename(exam.workspace)
    write_json(exam.root/'active_problem.json',{'id':problem_id})
    exam.store.event('problem_selected',problem_id=problem_id,version=problem.manifest['version'],digest=report['digest'],
                     backup=str(backup.relative_to(exam.root)) if backup else None)
    return {**problem.summary(),'selected':True,'backup':str(backup) if backup else None}


def new_session(exam,problem_id,destination):
    problem=Problem(exam.root,problem_id);problem.validate()
    target=Path(destination).resolve()
    if target.exists():raise ValueError('새 회차 경로가 이미 존재합니다. 비어 있는 새 이름을 지정하세요.')
    if target.is_relative_to(exam.root):raise ValueError('새 회차는 현재 시험 폴더 밖에 만드세요.')
    skip={'quality_runs','logs','analysis','reports','submissions','runtime','agent_runs','workspace','backups','__pycache__','.git','.venv','node_modules','active_problem.json'}
    shutil.copytree(exam.root,target,ignore=lambda p,n:[x for x in n if x in skip or x.endswith('.pyc')])
    from .engine import Exam
    new=Exam(target);select_problem(new,problem_id)
    return {'directory':str(target),'problem_id':problem_id,'started':False,'command':'python exam.py serve --open'}


def self_test(exam,problem_id):
    problem=Problem(exam.root,problem_id);validation=problem.validate()
    with tempfile.TemporaryDirectory(prefix='cofa-reference-') as temp:
        ws=Path(temp)/'workspace';copy_workspace(problem.file('reference_dir'),ws)
        result=exam.run_command(['{python}','{root}/tools/case_runner.py','--pack',str(problem.path),'--workspace','{workspace}','--suite','private'],ws,
                                name='출제자 참조 구현 검증',scope='author_reference',visibility='internal',
                                timeout_seconds=max(90, validation['cases']['private']*float(problem.manifest['runner'].get('case_timeout_seconds',5))+30))
    try:detail=json.loads(result['stdout'])
    except (ValueError,KeyError):detail={'error':result.get('stderr')}
    return {'validation':validation,'reference_passed':result['passed'],'result':detail}


def debrief(exam):
    if exam.session()['phase']!='evaluated':raise RuntimeError('정답 공개는 최종 평가 후에만 가능합니다.')
    problem=active_problem(exam)
    if not problem:raise RuntimeError('선택된 문제팩이 없습니다.')
    folder=exam.root/'reports'/'debrief'
    if folder.exists():return {'directory':str(folder),'already_exported':True}
    copy_workspace(problem.path,folder)
    ids=sorted(set(exam.store.get('disclosed_facts',[]) + [x for e in exam.store.events() if e['kind']=='requirement_disclosed' for x in e['data'].get('fact_ids',[])]))
    write_json(folder/'session_disclosure.json',{'asked_requirement_ids':ids,'note':'질문 개수나 공개 개수만으로 점수를 주지 않습니다.'})
    exam.store.event('answer_key_opened','system',problem_id=problem.manifest['id'])
    return {'directory':str(folder),'solution':str(folder/problem.manifest['solution']),
            'reference':str(folder/problem.manifest['reference_dir']),'disclosed_ids':ids}


def create_draft(exam,problem_id,title):
    if not ID.fullmatch(problem_id):raise ValueError('문제 ID 형식 오류')
    path=exam.root/'problem_packs'/problem_id
    if path.exists():raise ValueError('이미 있는 문제 ID입니다.')
    for name in ('public','private/reference','private/cases','public_cases'):(path/name).mkdir(parents=True,exist_ok=True)
    m={'schema_version':1,'id':problem_id,'version':'0.1.0','title':title or problem_id,'track':'client','ready':False,
       'description':'출제 초안: 명세·참조 구현·독립 기대값을 작성하고 검증하세요.',
       'public_dir':'public','truth':'private/truth.json','solution':'private/SOLUTION.md',
       'interview_guide':'private/INTERVIEW_GUIDE.md','reference_dir':'private/reference',
       'stakeholder_name':'가상 현업 담당자',
       'checks':[
           {'name':'공개 연결 검사','phase':'development','scope':'problem_public','timeout_seconds':60,'argv':['{python}','{root}/tools/case_runner.py','--pack','{pack}','--workspace','{workspace}','--suite','public']},
           {'name':'최종 인수 검사','phase':'submission','scope':'problem_hidden','timeout_seconds':300,'argv':['{python}','{root}/tools/case_runner.py','--pack','{pack}','--workspace','{workspace}','--suite','private']},
           {'name':'응시자 테스트','phase':'both','scope':'candidate','argv':['{python}','-m','unittest','discover','-s','tests','-v']}],
       'suites':{'public':'public_cases/cases.json','private':'private/cases/cases.json'},
       'runner':{'argv':['{python}','{workspace}/app.py'],'case_timeout_seconds':5},'manual_checks':['출제자가 별도 확인할 항목을 적으세요.']}
    write_json(path/'problem.json',m)
    write_json(path/'private/truth.json',{'schema_version':1,'persona':m['stakeholder_name'],
        'facts':[{'id':'R01','topic':'업무 목표','answer':'TODO: 확정된 업무 조건을 작성하세요.',
                  'keywords':['목표','사용자'],'visibility':'overview','kind':'required'}],
        'disclosure':{'overview_ids':['R01'],'mode':'progressive','max_new_facts_per_turn':3},'grading_notes':'참조 구현과 동일 구조를 강요하지 마세요.'})
    for name,content in {
        'public/MISSION.md':'# TODO: 모호하지만 해결 가능한 업무 요청\n구체적인 판정 조건은 담당자에게 확인하도록 설계하세요.',
        'public/CONTRACT.md':'# TODO: 공개 입출력 계약\n기계가 연결할 argv/입력/출력 구조를 적으세요.',
        'private/SOLUTION.md':'# TODO: 확정 명세·풀이·대안·한계',
        'private/INTERVIEW_GUIDE.md':'# TODO: 제출 코드에 연결할 면담 질문과 검증 포인트',
        'private/reference/app.py':'raise SystemExit("TODO: 참조 구현을 작성하세요.")',
        'public/app.py':'raise SystemExit("NOT_IMPLEMENTED")',
        'public/README.md':'MISSION.md를 읽고 담당자에게 질문하세요.',
        'AUTHOR_ONLY.md':'초안입니다. ready=false 상태에서는 선택할 수 없습니다. 독립 기대값을 먼저 정의하고 참조 구현을 검증하세요.'
    }.items():(path/name).write_text(content+'\n',encoding='utf-8')
    for name in ('PLAN.md','ENV.md','AGENTS.md','HANDOFF.md'):
        (path/'public'/name).write_text('# '+name+'\n\n작성할 항목을 정의하세요.\n',encoding='utf-8')
    stub={'schema_version':1,'cases':[{'id':'replace-this-case','requirement_ids':['R01'],
                                    'input':{},'expected_exit':0,'expected':{'TODO':'작성 필요'}}]}
    write_json(path/'public_cases/cases.json',stub);write_json(path/'private/cases/cases.json',stub)
    return {'created':str(path),'ready':False,'next':'문제/정답/테스트를 채운 뒤 ready=true, problem validate, problem self-test --author 순서로 확인하세요.'}
