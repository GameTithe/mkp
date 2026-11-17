"""Developer-only DOM regression, isolated mock API (NOT live Codex/HTTP E2E).
Requires playwright solely for running this optional verification tool.
"""
from pathlib import Path
import argparse
import json
import shutil
from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    markup = (root/'web/index.html').read_text(encoding='utf-8')
    markup = markup.replace('<link rel="stylesheet" href="/style.css">', '').replace('<script src="/app.js"></script>', '')
    steps, errors = [], []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=shutil.which('chromium') or p.chromium.executable_path, args=['--no-sandbox'])
        page = browser.new_page(viewport={'width': 1280, 'height': 900})
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.set_content(markup)
        page.add_style_tag(path=str(root/'web/style.css'))
        page.evaluate('''() => {
          Object.defineProperty(window, 'sessionStorage', {value:{getItem:()=>'',setItem:()=>{}}});
          window.confirm=()=>true;
          window.fixture={phase:'development',mode:'demo',jobs:[],development_remaining:7200,interview_remaining:1800,
            problem:{id:'client-frame-regression',title:'UI 상태 검사 전용 / 실제 시험 아님',version:'2.0.1',stakeholder_name:'담당자'},
            assessment:{status:'not_started'}};
          window.fixtureEvents=[{id:'E00000001',seq:1,kind:'message',channel:'stakeholder',at:1,data:{actor:'candidate',text:'p95 정의는 무엇인가요?'}}];
          window.actions=[];
          window.fetch=async (path, options={})=>{
            let data={}, ok=true;
            if(path==='/api/state')data=structuredClone(window.fixture);
            else if(path.startsWith('/api/events')){const after=Number((path.split('after=')[1])||0);data=fixtureEvents.filter(e=>e.seq>after);}
            else if(path==='/api/problems')data=[{...fixture.problem,ready:true}];
            else if(path==='/api/files')data={files:['MISSION.md']};
            else if(path.startsWith('/api/file?'))data={content:'UI fixture only'};
            else if(path==='/api/tasks')data=[];
            else if(path==='/api/feedback-text'){
              ok=['completed','evaluated'].includes(fixture.phase);
              data=ok?{text:'채점 실패 / 검사 결과와 면담 기록 보존 / 합성 UI 점검 화면'}:{error:'시험 중 공개 금지'};
            }
            else if(path==='/api/action'){
              let params=JSON.parse(options.body);actions.push(params);
              let job={id:'job'+actions.length,action:params.action,status:'done',result:{fixture:true}};
              if(params.action==='evaluate'){
                fixture.phase='completed';fixture.assessment={status:'failed',attempt:1,error:'SYNTHETIC_OUTAGE'};
                job.status='error';job.error='SYNTHETIC_OUTAGE';delete job.result;
              }
              fixture.jobs=[job];data={job:job.id};
            }
            return {ok,statusText:ok?'OK':'Forbidden',json:async()=>data};
          };
        }''')
        page.add_script_tag(path=str(root/'web/app.js'))
        page.wait_for_function("document.getElementById('phase').textContent==='개발'")
        assert page.locator('#show-feedback').is_disabled()
        steps.append('feedback blocked during development')
        assert page.locator('#review-question').is_enabled()
        page.locator('#review-question').click()
        page.wait_for_function("actions.some(x=>x.action==='stakeholder_reconsider')")
        steps.append('original-question reconsideration button routes correctly')
        page.evaluate("fixture.phase='completed'; fixture.jobs=[]; poll()")
        page.wait_for_function("!document.getElementById('show-feedback').disabled")
        assert page.locator('#feedback-retry').is_enabled()
        page.locator('#show-feedback').click()
        page.wait_for_function("document.getElementById('feedback-text').textContent.includes('면담 기록 보존')")
        steps.append('completed-before-scoring allows factual feedback and retry')
        page.locator('[data-action="evaluate"]').click()
        page.wait_for_function("document.getElementById('assessment-status').textContent.includes('채점 실패')")
        assert not page.locator('#feedback-panel').is_hidden()
        steps.append('failed evaluation shows failure status and factual feedback')
        page.locator('[data-tab="feedback"]').click()
        page.locator('#compose').fill('실패한 채점을 다시 실행하려면?')
        page.locator('#send').click()
        page.wait_for_function("actions.some(x=>x.action==='feedback_chat')")
        steps.append('post-exam coach routing remains available after scoring failure')
        page.evaluate("fixture.phase='evaluated'; fixture.assessment={status:'succeeded'}; fixture.jobs=[]; poll()")
        page.wait_for_function("!document.querySelector('[data-action=problem_debrief]').disabled")
        steps.append('answer disclosure remains gated until evaluated')
        page.set_viewport_size({'width':390,'height':844})
        page.wait_for_timeout(100)
        width=page.evaluate('document.documentElement.scrollWidth')
        assert width <= 390, width
        steps.append('390px layout has no horizontal overflow')
        browser.close()
    result={'passed':not errors,'kind':'isolated Chromium DOM with memory mock API',
            'steps':steps,'errors':errors,'browser_to_live_backend_tested':False,
            'live_llm_tested':False,'document_width':width,'viewport_width':390}
    path=Path(args.output);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))
    if errors: raise SystemExit(1)

if __name__=='__main__':main()
