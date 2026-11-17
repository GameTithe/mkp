'use strict';
const $ = id => document.getElementById(id);
const token = new URLSearchParams(location.hash.slice(1)).get('token') || sessionStorage.getItem('cofaToken') || '';
sessionStorage.setItem('cofaToken', token);
if(location.hash) history.replaceState(null,'',location.pathname);
let tab='stakeholder', sequence=0, events=[], state={}, completedJobs=new Set(), polling=false, activeProblem=null, modelInitialized=false;
async function api(path, payload) {
 const r=await fetch(path,{method:payload?'POST':'GET',headers:{'X-COFA-Token':token,'Content-Type':'application/json'},body:payload?JSON.stringify(payload):undefined});
 const data=await r.json(); if(!r.ok) throw Error(data.error||r.statusText); return data;
}
function notify(message){$('notification').textContent=message;}
async function action(name,params={}) { try {const r=await api('/api/action',{action:name,...params});notify('실행 중: '+name+' · '+r.job);await poll();} catch(e){notify(e.message);} }
function clock(seconds){seconds=Math.max(0,Math.floor(seconds||0));return `${Math.floor(seconds/60).toString().padStart(2,'0')}:${(seconds%60).toString().padStart(2,'0')}`;}
const phases={not_started:'시작 전',development:'개발',submitted:'제출 완료',interview:'사후 면담',completed:'시험 완료 · 채점 미완료',evaluated:'평가 완료'};
function renderMessages(){
 const box=$('messages');box.replaceChildren();
 for(const e of events){
  let content=null,actor=e.data.actor||'system';
  if(e.kind==='message') content=e.data.text;
  else if(tab==='all' && ['command_result','delegation','merge','merge_conflict','note','model_change','operation_error','submission','interview_finish'].includes(e.kind)) content=JSON.stringify(e.data,null,2);
  else if(e.kind==='codex_event' && tab==='all') {let item=e.data.event.item;if(item&&item.type!=='agent_message')content=JSON.stringify(e.data.event,null,2);}
  if(content===null || (tab!=='all'&&e.channel!==tab))continue;
  const d=document.createElement('div');d.className='bubble '+actor;
  const label=document.createElement('label');label.textContent=`${e.id} · ${e.channel} · ${actor} · ${new Date(e.at*1000).toLocaleTimeString()}`;
  const pre=document.createElement('pre');pre.textContent=content;d.append(label,pre);box.append(d);
 }
 box.scrollTop=box.scrollHeight;
}
function chooseTab(name){tab=name;document.querySelectorAll('[data-tab]').forEach(b=>b.classList.toggle('selected',b.dataset.tab===tab));
 $('target').textContent='대상: '+({stakeholder:'현업 담당자',requirements:'내가 정리한 요구사항 확인 (한 줄에 한 문장)',feedback:'시험 종료 복습 코치',interview:'면담 평가자',all:'전체 기록(읽기 전용)'}[tab]);
 $('compose').placeholder=tab==='requirements'?'이해한 조건을 한 줄에 하나씩 적으세요. 전체 정답 문서를 자동으로 만들어주지는 않습니다.':tab==='feedback'?'놓친 질문·구현·검증·면담 답변에 대해 복습 질문을 하세요.':tab==='interview'?'본인이 직접 답하세요. 제출본과 실제 동작에 근거해 설명하세요.':'질문 또는 작업 지시를 입력하세요.';
 renderMessages();updateControls();}
function updateControls(){
 const busy=(state.jobs||[]).some(j=>j.status==='running');
 const running=(state.jobs||[]).find(j=>j.status==='running'),activity=$('activity');
 const activityNames={ask:'현업 담당자가 답변 중입니다…',requirements_confirm:'요구사항을 확인하는 중입니다…',feedback_chat:'복습 코치가 답변 중입니다…',qa_answer:'다음 면담 질문을 준비하는 중입니다…'};
 activity.hidden=!running;
 if(running)activity.querySelector('strong').textContent=activityNames[running.action]||`작업 실행 중: ${running.action}`;
 const dev=state.phase==='development'&&!state.development_expired;
 const interview=state.phase==='interview'&&!state.interview_expired;
 $('send').disabled=busy||tab==='all'||(tab==='interview'?!interview:tab==='feedback'?!['completed','evaluated'].includes(state.phase):!dev);
 $('savefile').disabled=busy||!dev;$('editor').readOnly=!dev;
 $('start').disabled=busy||state.phase!=='not_started';
 $('startmode').disabled=busy||state.phase!=='not_started';
 $('model').disabled=busy||!['not_started','development'].includes(state.phase);
 $('set-model').disabled=busy||!dev;
 $('open-cli').disabled=busy||!dev||state.mode!=='llm';
 $('select-problem').disabled=busy||state.phase!=='not_started';
 $('problem-select').disabled=busy||state.phase!=='not_started';
 for(const id of ['run','addnote','merge','parallel','submit'])$(id).disabled=busy||!dev;
 $('finish').disabled=busy||!interview;
 $('show-feedback').disabled=busy||!['completed','evaluated'].includes(state.phase);
 $('feedback-retry').disabled=busy||!['completed','evaluated'].includes(state.phase);
 $('review-question').disabled=busy||!dev||!events.some(e=>e.kind==='message'&&e.channel==='stakeholder'&&e.data.actor==='candidate'&&!e.data.reconsideration);
 $('link-claim').disabled=busy||!dev;
 document.querySelectorAll('[data-action]').forEach(b=>{
  const a=b.dataset.action;let enabled=true;
  if(['check','audit'].includes(a))enabled=dev;
  if(a==='qa_start')enabled=state.phase==='submitted';
  if(a==='qa_retry')enabled=interview&&!state.question;
  if(a==='evaluate')enabled=['completed','evaluated'].includes(state.phase);
  if(a==='problem_debrief')enabled=state.phase==='evaluated';
  b.disabled=busy||!enabled;
 });
}
async function refreshFiles(){try{const d=await api('/api/files');$('filelist').replaceChildren();for(const path of d.files){const b=document.createElement('button');b.textContent=path;b.onclick=()=>openFile(path);$('filelist').append(b);}}catch(e){notify(e.message);}}
async function openFile(path){try{const d=await api('/api/file?path='+encodeURIComponent(path));$('filepath').value=path;$('editor').value=d.content;}catch(e){notify(e.message);}}
async function poll(){
 if(polling)return;polling=true;
 try{
  state=await api('/api/state');
  if(!modelInitialized){$('model').value=state.model||'';modelInitialized=true;}
  $('problem-title').textContent=state.problem?state.problem.title+' · v'+state.problem.version:'문제팩을 선택하세요';
  $('stakeholder-name').textContent=state.problem?state.problem.stakeholder_name:'';
  if(state.problem&&activeProblem!==state.problem.id&&$('problem-select').options.length){$('problem-select').value=state.problem.id;activeProblem=state.problem.id;}
  $('phase').textContent=phases[state.phase]||state.phase;
  const evaluation=state.assessment||{};
  $('assessment-status').textContent=evaluation.status==='failed'?'채점 실패 · 시험 기록은 보존되어 있습니다. 종료 피드백을 확인하고 최종 평가를 재시도하세요.':state.phase==='completed'?'시험은 끝났습니다. 채점 전에도 사실 피드백을 볼 수 있습니다.':evaluation.status==='succeeded'?'평가 완료 · 미확정 항목은 사유별로 표시됩니다.':'';
  $('timer').textContent=clock(state.phase==='interview'?state.interview_remaining:state.development_remaining);
  $('mode').textContent=state.mode==='demo'?'DEMO · 실제 LLM/능력 점수 없음':`120분 개발 + 30분 면담 · ${state.model||'Codex 기본 모델'}`;document.body.classList.toggle('demo',state.mode==='demo');
  $('question').textContent=state.phase==='interview'&&state.question?'현재 질문: '+state.question.question:'';
  const newer=await api('/api/events?after='+sequence);
  if(newer.length){sequence=Math.max(...newer.map(x=>x.seq));events.push(...newer);renderMessages();if(newer.some(x=>x.kind==='file_changes'))await refreshFiles();}
  for(const job of state.jobs||[]){if(job.status!=='running'&&!completedJobs.has(job.id)){completedJobs.add(job.id);$('result').textContent=JSON.stringify(job.result||{error:job.error},null,2);notify(job.status==='error'?job.error:'완료: '+job.action);await refreshFiles();if(job.action==='qa_start'&&job.status==='done')chooseTab('interview');if(['evaluate','feedback','qa_finish'].includes(job.action)&&['completed','evaluated'].includes(state.phase))await showFeedback();if(job.action==='start'&&job.status==='done'){chooseTab('stakeholder');openFile('MISSION.md');if(state.mode==='llm')action('open_cli');}}}
  updateControls();
 }catch(e){notify(e.message);}finally{polling=false;}
}
document.querySelectorAll('[data-tab]').forEach(b=>b.onclick=()=>chooseTab(b.dataset.tab));
document.querySelectorAll('[data-action]').forEach(b=>b.onclick=()=>action(b.dataset.action));
$('start').onclick=()=>{if(confirm('120분 타이머를 시작합니다. LLM 모드는 Codex 로그인이 필요합니다.')){const params={mode:$('startmode').value},model=$('model').value.trim();if(model)params.model=model;action('start',params);}};
$('set-model').onclick=()=>action('model',{model:$('model').value.trim()||null});
$('open-cli').onclick=()=>action('open_cli');
$('send').onclick=()=>{const text=$('compose').value;if(!text.trim())return;const a={stakeholder:'ask',requirements:'requirements_confirm',feedback:'feedback_chat',interview:'qa_answer'}[tab];if(a){$('compose').value='';action(a,{text});}};
$('compose').onkeydown=e=>{if(e.ctrlKey&&e.key==='Enter'){e.preventDefault();if(!$('send').disabled)$('send').click();}};
$('refresh').onclick=refreshFiles;$('openfile').onclick=()=>openFile($('filepath').value);
$('savefile').onclick=()=>action('save_file',{path:$('filepath').value,content:$('editor').value});
$('run').onclick=()=>{try{const argv=JSON.parse($('command').value);if(!Array.isArray(argv))throw Error('JSON 인자 배열을 입력하세요.');action('run',{argv});}catch(e){notify(e.message);}};
$('addnote').onclick=()=>action('note',{category:$('notetype').value,text:$('note').value});
$('merge').onclick=()=>action('merge',{id:$('mergeid').value.trim()});
$('parallel').onclick=async()=>{try{const tasks=JSON.parse($('tasks').value);if(!Array.isArray(tasks))throw Error('작업 JSON 배열을 입력하세요.');if(confirm('편집한 역할·작업을 별도 복사본에서 병렬 실행합니다. 여러 Codex 호출이 발생합니다.'))action('parallel',{tasks});}catch(e){notify(e.message);}};
$('submit').onclick=()=>{if(confirm('현재 구현을 제출하고 개발 기능을 종료합니다. 제출 이후에는 동결본으로 면담·평가합니다.'))action('submit');};
$('finish').onclick=()=>{if(confirm('30분이 남아 있어도 면담을 종료합니다. 종료 이유와 시각이 기록됩니다.'))action('qa_finish');};
api('/api/tasks').then(tasks=>{$('tasks').value=JSON.stringify(tasks,null,2);}).catch(e=>notify(e.message));
$('select-problem').onclick=()=>{if(confirm('작업 폴더를 백업하고 선택한 문제의 초기 파일로 바꿉니다.'))action('problem_select',{id:$('problem-select').value});};
api('/api/problems').then(items=>{for(const p of items){const option=document.createElement('option');option.value=p.id;option.textContent=(p.title||p.id)+(p.ready?'':' [초안]');option.disabled=!p.ready;$('problem-select').append(option);}}).catch(e=>notify(e.message));
$('usage-refresh').onclick=async()=>{try{const d=await api('/api/usage');$('usage-result').textContent=JSON.stringify(d.groups,null,2);}catch(e){notify(e.message);}};
refreshFiles();poll();setInterval(poll,1000);

async function showFeedback(){
 try{const d=await api('/api/feedback-text');$('feedback-panel').hidden=false;$('feedback-text').textContent=d.text||'피드백 파일이 없습니다. 피드백 재생성 버튼으로 사실 기록을 생성하세요.';$('feedback-panel').scrollIntoView({behavior:'smooth'});}catch(e){notify(e.message);}
}
$('show-feedback').onclick=showFeedback;
$('review-question').onclick=()=>{if(confirm('마지막 담당자 질문을 원래 문맥으로 한 번 재검토합니다. 추가 조건을 요구하는 기능이 아니며 타이머는 계속 흐릅니다.'))action('stakeholder_reconsider');};
$('feedback-retry').onclick=()=>action('feedback',{force:true});
$('link-claim').onclick=()=>action('requirements_link',{claim_id:$('claim-id').value.trim(),stage:$('claim-stage').value,evidence_id:$('claim-evidence').value.trim()});
