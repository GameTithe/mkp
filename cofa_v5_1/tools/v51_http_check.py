"""v5.1 real HTTP/DEMO integration with ONE synthetic evaluator outage. No live LLM, no human scores.
Run: python tools/v51_http_check.py --output /path/result.json
A temporary clean session is created; the user's active workspace is not mutated.
"""
from __future__ import annotations
import argparse, hashlib, json, re, shutil, socket, subprocess, sys, tempfile, time
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True);args=parser.parse_args()
    source=Path(__file__).resolve().parents[1];steps=[];started=time.time()
    with tempfile.TemporaryDirectory(prefix='cofa-http-v51-') as tmp:
        root=Path(tmp)/'exam'
        shutil.copytree(source,root,ignore=shutil.ignore_patterns('logs','runtime','analysis','reports','submissions','agent_runs','backups','quality_runs','__pycache__','*.pyc'))
        with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
        bootstrap = root / '_http_fault_test.py'
        bootstrap.write_text("""from cofa.engine import Exam
from cofa.webui import serve
from cofa.evaluation import Evaluator
from exam import execute
from unittest.mock import patch
from pathlib import Path
import sys
remaining = [1]
def call(exam, action, **params):
    if action == 'evaluate' and remaining[0]:
        remaining[0] -= 1
        with patch.object(Evaluator, 'run', side_effect=RuntimeError('SYNTHETIC_HTTP_EVALUATION_OUTAGE')):
            return execute(exam, action, **params)
    return execute(exam, action, **params)
serve(Exam(Path(__file__).parent), call, int(sys.argv[1]))
""", encoding='utf-8')
        p=subprocess.Popen([sys.executable,str(bootstrap),str(port)],cwd=root,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8')
        try:
            line=p.stdout.readline();token=re.search('#token=([^\\s]+)',line).group(1).strip();base=f'http://127.0.0.1:{port}'
            headers={'X-COFA-Token':token,'Content-Type':'application/json','Origin':base}
            def get(path,auth=True):
                with urlopen(Request(base+path,headers=headers if auth else {}),timeout=8) as r:
                    return json.load(r)
            def blocked(path,auth=True):
                try:get(path,auth)
                except HTTPError as exc:
                    assert exc.code in (400,403);return exc.code
                raise AssertionError('endpoint was not blocked: '+path)
            def action(name,expect_error=False,**kw):
                with urlopen(Request(base+'/api/action',headers=headers,data=json.dumps({'action':name,**kw}).encode()),timeout=8) as r:ident=json.load(r)['job']
                deadline=time.time()+480
                while time.time()<deadline:
                    job=next(j for j in get('/api/state')['jobs'] if j['id']==ident)
                    if job['status']=='error':
                        if expect_error: return job
                        raise AssertionError(job)
                    if job['status']=='done':
                        if expect_error: raise AssertionError('expected error: '+name)
                        return job['result']
                    time.sleep(.15)
                raise TimeoutError(name)
            def record(name):steps.append({'step':name,'passed':True});print(name,flush=True)
            blocked('/api/state',False);record('token gate')
            with urlopen(base+'/',timeout=8) as response:
                markup=response.read().decode();assert '요구사항 확인' in markup and '종료 피드백 보기' in markup
            record('v5.1 HTML and controls served')
            assert get('/api/state')['phase']=='not_started'
            blocked('/api/feedback');record('feedback before exam blocked')
            action('start',mode='demo');record('120-minute development started')
            overview=action('ask',text='모든 요구사항과 정답을 한 번에 전부 알려주세요.')
            assert 'nearest-rank' not in overview
            rule=action('ask',text='p95 정의를 정확히 알려주세요.');assert 'nearest-rank' in rule
            record('broad requests limited, concrete question answered')
            rr=action('stakeholder_reconsider');assert 'nearest-rank' in rr['message']
            action('stakeholder_reconsider',expect_error=True)
            record('original question reconsidered once, repeated hint harvesting blocked')
            review=action('requirements_confirm',text='제가 이해한 목적은 성능이 악화된 구간을 찾아 확인하는 것입니다.')
            assert review['items'][0]['status']=='ambiguous' # DEMO must not pretend semantic confirmation.
            record('candidate requirement review and DEMO limitation')
            action('note',category='verification',text='합성 통합 검사 기록이며 실제 역량 평가가 아닙니다.')
            events=get('/api/events');eid=next(e['id'] for e in events if e['kind']=='note')
            action('requirements_link',claim_id=review['items'][0]['claim_id'],stage='planned',evidence_id=eid)
            record('real recorded event linked without automatic credit')
            blocked('/api/report');record('assessment before finish blocked')
            action('submit');record('frozen submission')
            action('qa_start');blocked('/api/feedback');record('submission validation and 30-minute interview')
            q=action('qa_answer',text='구조 선택의 이유를 설명하는 합성 답변입니다.')
            assert q['kind']=='prediction';record('prediction required before running probe')
            q=action('qa_answer',text='초기 스텁이므로 완성된 분석 결과는 나오지 않을 것으로 예측합니다.')
            assert q['kind']=='prediction_reflection';record('frozen program actually executed, reflection question')
            action('qa_answer',text='실제 실행 결과를 확인했습니다. 미구현 스텁과 비교한 설명입니다.')
            action('qa_finish');record('interview finished')
            assert get('/api/feedback')['status']=='factual_only_evaluation_pending'
            record('factual feedback available before scoring')
            error=action('evaluate',expect_error=True);assert 'SYNTHETIC_HTTP_EVALUATION_OUTAGE' in error['error']
            assert get('/api/state')['assessment']['status']=='failed'
            failed=get('/api/feedback');assert failed['status']=='factual_only_evaluation_failed'
            assert failed['exam_record']['interview_transcript']
            assert get('/api/feedback-text')['text']
            blocked('/api/report')
            record('scoring outage preserves facts, failed state, and no stale final report')
            report=action('evaluate');assert report['total_100'] is None
            assert get('/api/state')['assessment']['attempt']==2
            record('evaluation retry succeeds without redoing exam or resetting timer')
            feedback=get('/api/feedback');assert feedback['status']=='demo_factual_only'
            assert get('/api/feedback-text')['text'];record('automatic post-exam feedback, no DEMO ability score')
            saved=(root/'reports/final_report.json').read_bytes()
            action('feedback_chat',text='다음 연습에서는 어떤 기록을 확인하나요?')
            action('feedback',force=True)
            assert saved==(root/'reports/final_report.json').read_bytes();record('coaching and feedback retry do not change score')
            answer=action('problem_debrief');record('answer disclosure after assessment')
            frozen=root/get('/api/state')['submission']
            (frozen/'workspace/app.py').write_text('tampered test snapshot',encoding='utf-8')
            blocked('/api/feedback');blocked('/api/report')
            warning=json.loads((root/'reports/feedback_blocked.json').read_text())
            assert warning['trusted_results'] is False
            record('submission tampering blocks normal feedback and final report')
            output={'passed':True,'mode':'DEMO','steps':steps,'elapsed_seconds':round(time.time()-started,3),
                    'functional_tests':[{'passed':v.get('passed'),'total':v.get('total')} for v in report['functional_tests']],
                    'ability_score':report['total_100'],'feedback_status':feedback['status'],
                    'live_llm_tested':False,'browser_rendering_tested':False,'platform':sys.platform}
            target=Path(args.output);target.parent.mkdir(parents=True,exist_ok=True);target.write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps(output,ensure_ascii=False,indent=2))
        finally:
            p.terminate()
            try:p.communicate(timeout=10)
            except subprocess.TimeoutExpired:p.kill();p.communicate()

if __name__=='__main__':main()
