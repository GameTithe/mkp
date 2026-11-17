# COFA v5 추가 검토 기록

검토일: 2026-11-16. 원본 ZIP을 별도 폴더에 풀어 읽고, 메모리 내 테스트 대역으로 경계 조건을 확인했습니다. 배포본 실행 코드·프롬프트·문제팩은 수정하지 않았습니다.

## 대상
- 파일: `cofa_mock_v5_stakeholder_evaluation_feedback_client_120plus30.zip`
- SHA-256: `59e7b8cb9d0f19ab6da6f36bac6a304c90ad2d4bcb153dafc5e992d3d38150b3`
- 이번 검사는 전체 회귀 165개 재실행이 아닙니다. 실제 Codex, 원격 LLM, Windows, 150분 연속 운용은 검사하지 않았습니다.
- 아래 합성 결과는 사용자 능력 점수나 실제 모델의 오류율이 아닙니다. 문제의 조건과 실행 경로를 확인하기 위한 경계 검사입니다.

## 1. 품질 기준 사례가 최종 검토 실패를 일치로 표시

초기 점수 ratings를 review_axis 실행 전에 추가하고, matches_authored_fixture는 그 초기 점수만 검사합니다. review가 점수를 null로 보류하거나 예외가 발생해도 초기 점수의 개수/범위가 맞으면 일치로 집계됩니다. 합성 검토 응답으로 두 경우를 재현했습니다. 최종 사용자 채점 점수가 잘못 확정됐다는 재현이 아니라, 채점기를 점검하는 quality live의 일치 지표 결함입니다.
제안: raw_match와 reviewed_match를 분리하고, 최종 검토 상태와 errors를 함께 확인합니다. 판단 보류/호출 실패는 통과가 아닌 별도 상태로 기록합니다.

근거: `cofa/benchmarks.py:65-95`
```python
65:     for case in data['judge_cases']:
66:         bundle={'atoms':case['atoms'],'coverage':{'synthetic':True}};ratings=[];checked=[];errors=[]
67:         for iteration in range(repeats):
68:             payload=live.prompt('judge')+'\nAXIS='+case['axis']+'\n평가기준:\n'+canonical(RUBRIC[case['axis']])+canonical(ANCHORS)+canonical(TOP_ANCHORS)+'\n<untrusted_evidence>\n'+canonical(bundle)+'\n</untrusted_evidence>'
69:             try:
70:                 response=live.provider().run(payload,folder,'benchmark:judge',schema=exam.root/'schemas/axis_score.json')
71:                 grade=Evaluator(live).validate_axis(parse_json(response['text']),case['axis'],bundle)
72:                 selected=next(x for x in grade['criteria'] if x['id']==case['criterion']);ratings.append(selected['rating'])
73:                 reviewed=review_axis(live,grade,bundle,[])
74:                 checked.append(next(x for x in reviewed['criteria'] if x['id']==case['criterion']))
75:             except Exception as exc:errors.append(str(exc))
76:         obj={'id':case['id'],'expected_ratings':case['expected_ratings'],'ratings':ratings,'reviews':checked,
77:              'matches_authored_fixture':len(ratings)==repeats and all(x in case['expected_ratings'] for x in ratings),
78:              'repeat_disagreement':len(set(ratings))>1,'errors':errors}
79:         output['judge'].append(obj);write_json(dest,output)
80:     problem=exam.problem();truth=problem.truth()
81:     sp=folder/'context_route.json';write_json(sp,route_schema(truth['facts']))
82:     for case in data['stakeholder_cases']:
83:         body={'persona':truth['persona'],'facts':truth['facts'],'question':case['question'],
84:               'history':case.get('history',[]),'known_fact_ids':case.get('known_fact_ids',[])}
85:         item={'id':case['id']}
86:         try:
87:             answer=live.provider().run(POLICY+'\nCONTEXT_INPUT_JSON\n'+canonical(body),folder,'benchmark:stakeholder',schema=sp)
88:             route=validate_route(parse_json(answer['text']),truth)
89:             text,ids,deferred=render_route(route,truth,body['known_fact_ids'],body['history'])
90:             item.update(route=route,disclosed=ids,text=text,matches_authored_fixture=
91:                         route['intent'] in case['expected_intents'] and set(case['must_include'])<=set(ids) and not set(case['must_exclude'])&set(ids))
92:         except Exception as exc:item.update(error=str(exc),matches_authored_fixture=False)
93:         output['stakeholder'].append(item);write_json(dest,output)
94:     output['fixture_match_count']=sum(x['matches_authored_fixture'] for x in output['judge']+output['stakeholder'])
95:     output['fixture_total']=len(output['judge'])+len(output['stakeholder'])
```

## 2. 최종 문맥에 코드 원문이 면담 원문보다 먼저 배정됨

주석은 코드/과정 발췌를 번갈아 고른다고 하지만 실제 sorted는 kind=source인 모든 근거를 먼저 둡니다. 5100자 예산의 합성 사례에서 코드 원문만 포함되고 응시자 면담 답변은 summary_only로 남았습니다. 모든 기록의 저장과 앞선 청크 분석이 사라진다는 뜻은 아닙니다. 기본 70000자에서도 같은 선택 알고리즘이며, 실제 120분 로그의 발생률은 측정하지 않았습니다.
제안: 평가축별로 본인 답변, 실제 실행, 코드, 의사결정, 반대 근거의 최소 원문 공간을 확보하고 추가 원문 조회로 보완합니다. C 항목 피드백에 응시자 답변 원문이 제공되는지 별도로 검사합니다.

근거: `cofa/evaluation.py:49-73`
```python
49: def pack_context(bundle, findings, max_chars=70000):
50:     """All mapped findings + a declared subset of original source excerpts.
51:     The full raw evidence was consumed in the mapping pass, and remains on disk.
52:     """
53:     atoms = {a['id']: a for a in bundle['atoms']}
54:     selected = []
55:     for finding in findings:
56:         for ref in finding['evidence_ids']:
57:             if ref not in selected:
58:                 selected.append(ref)
59:     if not selected:
60:         selected = list(atoms)[:6]
61:     data = {'coverage': bundle['coverage'], 'findings': findings,
62:             'raw_excerpts': [], 'summary_only_evidence_ids': selected[:],
63:             'note': '모든 원문은 앞선 청크 분석에서 처리됨. 이 단계는 전체 관찰 요약과 명시된 원문 일부를 봄.'}
64:     if len(canonical(data)) > max_chars:
65:         raise RuntimeError('요약 근거가 문맥 한도를 초과했습니다. 조용히 잘라내지 않았습니다. config의 evaluation_max_context_characters를 늘리세요.')
66:     # Alternate artifact and process excerpts so the final stage is not code-only.
67:     ordered = sorted(selected, key=lambda x: (atoms[x].get('kind') != 'source', selected.index(x)))
68:     for ref in ordered:
69:         trial = {**data, 'raw_excerpts': data['raw_excerpts'] + [atoms[ref]],
70:                  'summary_only_evidence_ids': [x for x in data['summary_only_evidence_ids'] if x != ref]}
71:         if len(canonical(trial)) <= max_chars:
72:             data = trial
73:     return canonical(data)
```

## 3. 최종 평가 자체가 실패하면 사실 피드백도 잠김

Evaluator.run이 실패하면 phase는 completed 상태에 남습니다. feedback.require_finished는 evaluated 상태만 허용하므로 시험/면담이 끝나도 사실 기반 종료 피드백을 여는 요청이 거절됩니다. 합성 평가 호출 실패로 상태와 게이트를 재현했습니다. 이것은 평가가 성공하고 피드백 생성 LLM만 실패하는 경로의 정상적인 fallback과 다른 문제입니다.
제안: 시험 완료와 채점 완료를 분리합니다. completed 단계에서 실제 테스트 결과·제출 시각·면담 기록·채점 실패 사유만 보여주는 최소 리포트를 생성하고, LLM 해석은 나중에 재시도합니다. 보안/무결성 오류는 정상 결과처럼 표시하지 말고 별도 차단·오류 리포트로 구분합니다.

근거: `cofa/engine.py:501-524`
```python
501:     def evaluate(self):
502:         state = self.session()
503:         if state['phase'] == 'interview' and time.time() >= state['interview']['deadline']:
504:             self.finish_interview('time_expired')
505:             state = self.session()
506:         if state['phase'] not in {'completed', 'evaluated'}:
507:             raise RuntimeError('제출 및 면담 종료 후에만 최종 평가할 수 있습니다.')
508:         from .evaluation import Evaluator
509:         result = Evaluator(self).run()
510:         state = self.session()
511:         state['phase'] = 'evaluated'
512:         state['report'] = 'reports/final_report.json'
513:         self.store.set('session', state)
514:         self.store.export()
515:         if self.config.get('auto_feedback'):
516:             from .feedback import generate
517:             try:
518:                 feedback = generate(self)
519:                 result['feedback'] = {'path':'reports/feedback.html', 'status':feedback['status']}
520:             except Exception as exc:
521:                 self.store.event('feedback_error', 'feedback', 'internal', error=str(exc))
522:                 result['feedback'] = {'status':'retry_required','error':str(exc)}
523:                 self.store.export()
524:         return result
```

피드백 잠금 추가 근거: `cofa/feedback.py:45-51`. 첫 검사에서 session.phase가 evaluated가 아니면 즉시 거절합니다.

## 설계상 추가 검토 지점 (실제 오채점 재현과 구분)

반복 채점의 작은 차이에도 해당 항목은 보류됩니다(cofa/quality.py:107-109). 하나의 항목이 보류되면 그 축과 전체 총점도 보류됩니다(cofa/evaluation.py:331-338). 이것은 확정 점수를 억지로 만들지 않기 위한 정책이나, 보류율을 실제 모델로 측정하고 불일치·근거 부족·환경 장애를 구분할 필요가 있습니다.

담당자의 새 기능을 늘리기 전에 실제 계정으로 자연어 동치 질문, 문맥을 잇는 짧은 질문, 잘못된 전제 정정, 과도 공개와 부당 거절을 각각 측정해야 합니다. 기존 보고서는 실제 LLM 의미 품질과 인간 기준 교정이 미검증이라고 명시합니다.

## 재현 출력
```json
{
  "excerpt_selection": {
    "raw_ids": [
      "F-code"
    ],
    "summary_only_ids": [
      "E-answer"
    ],
    "interview_raw_present": false,
    "note": "All findings remain. This is raw-excerpt bias, not loss of stored logs."
  },
  "benchmark_reviewer_rejects": {
    "raw_ratings": [
      3,
      3
    ],
    "reviewed_ratings": [
      null,
      null
    ],
    "matches_authored_fixture": true,
    "errors": []
  },
  "benchmark_reviewer_errors": {
    "raw_ratings": [
      3,
      3
    ],
    "review_count": 0,
    "matches_authored_fixture": true,
    "errors": [
      "synthetic review validation failure",
      "synthetic review validation failure"
    ]
  },
  "evaluation_failure_feedback_gate": {
    "phase": "completed",
    "interview_finished": true,
    "evaluation_error": "synthetic final evaluation call failure",
    "feedback_available": false,
    "feedback_error": "제출본 검사·사후 면담·최종 평가를 끝낸 뒤 피드백을 볼 수 있습니다."
  }
}
```

## 외부 근거 범위

- Cofa 공식 Probe: 산출물·과정·사후 답변을 연결하고 설명을 실제 구현과 대조하는 원칙을 확인했습니다. 위 코드 결함의 근거는 외부 문서가 아닌 로컬 소스와 재현입니다.
- OpenAI 공식 Evaluation best practices: 실제 사용 사례의 평가 데이터와 사람의 검토로 자동 평가를 교정하라는 지침을 참고했습니다. 이를 현재 채점기의 정확성 인증으로 사용하지 않았습니다.
