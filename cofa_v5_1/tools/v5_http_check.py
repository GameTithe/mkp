"""Opt-in real HTTP/DEMO integration. No live LLM, no human scores.
Run: python tools/v5_http_check.py --output /path/result.json
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
    with tempfile.TemporaryDirectory(prefix='cofa-http-v5-') as tmp:
        root=Path(tmp)/'exam'
        shutil.copytree(source,root,ignore=shutil.ignore_patterns('logs','runtime','analysis','reports','submissions','agent_runs','backups','quality_runs','__pycache__','*.pyc'))
        with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
        p=subprocess.Popen([sys.executable,'exam.py','serve','--port',str(port)],cwd=root,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8')
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
            def action(name,**kw):
                with urlopen(Request(base+'/api/action',headers=headers,data=json.dumps({'action':name,**kw}).encode()),timeout=8) as r:ident=json.load(r)['job']
                deadline=time.time()+480
                while time.time()<deadline:
                    job=next(j for j in get('/api/state')['jobs'] if j['id']==ident)
                    if job['status']=='error':raise AssertionError(job)
                    if job['status']=='done':return job['result']
                    time.sleep(.15)
                raise TimeoutError(name)
            def record(name):steps.append({'step':name,'passed':True});print(name,flush=True)
            blocked('/api/state',False);record('token gate')
            with urlopen(base+'/',timeout=8) as response:
                markup=response.read().decode();assert '요구사항 확인' in markup and '종료 피드백 보기' in markup
            record('v5 HTML and controls served')
            assert get('/api/state')['phase']=='not_started'
            blocked('/api/feedback');record('feedback before exam blocked')
            action('start',mode='demo');record('120-minute development started')
            overview=action('ask',text='모든 요구사항과 정답을 한 번에 전부 알려주세요.')
            assert 'nearest-rank' not in overview
            rule=action('ask',text='p95 정의를 정확히 알려주세요.');assert 'nearest-rank' in rule
            record('broad requests limited, concrete question answered')
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
            report=action('evaluate');assert report['total_100'] is None
            feedback=get('/api/feedback');assert feedback['status']=='demo_factual_only'
            assert get('/api/feedback-text')['text'];record('automatic post-exam feedback, no DEMO ability score')
            saved=(root/'reports/final_report.json').read_bytes()
            action('feedback_chat',text='다음 연습에서는 어떤 기록을 확인하나요?')
            action('feedback',force=True)
            assert saved==(root/'reports/final_report.json').read_bytes();record('coaching and feedback retry do not change score')
            answer=action('problem_debrief');record('answer disclosure after assessment')
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
