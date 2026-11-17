# 교체형 문제팩 제작 계약 v1

## 가장 짧은 교체 경로
새 문제의 폴더를 `problem_packs/<문제ID>/`에 넣고 `python exam.py problem validate <문제ID>` 후 `python exam.py problem select <문제ID>`.
시작한 회차에서는 `python exam.py problem new-session <문제ID> "../새폴더"`.
엔진, 담당자 코드, 평가 코드는 수정하지 않습니다.

`problem new 새문제ID --title "제목"`은 ready=false 초안을 생성합니다. 초안의 TODO를 완성하기 전에는 선택되지 않습니다.

## 1. problem.json — 유일한 연결 설정

```json
{
  "schema_version": 1,
  "id": "my-new-problem",
  "version": "1.0.0",
  "title": "새 문제",
  "track": "client",
  "ready": true,
  "description": "연습 문제 설명",
  "stakeholder_name": "가상 현업 담당자",
  "public_dir": "public",
  "truth": "private/truth.json",
  "solution": "private/SOLUTION.md",
  "interview_guide": "private/INTERVIEW_GUIDE.md",
  "reference_dir": "private/reference",
  "suites": {"public": "public_cases/cases.json", "private": "private/cases/cases.json"},
  "runner": {"argv": ["{python}", "{workspace}/app.py"], "case_timeout_seconds": 5},
  "checks": [],
  "manual_checks": ["별도 수동 검토 항목"]
}
```

위 예시는 필드 설명용입니다. 실제 checks는 생성된 초안 또는 포함된 문제팩의 세 항목을 복사해서 사용하세요.
phase는 development/submission/both, scope는 problem_public/problem_hidden/candidate를 사용합니다.
치환값: `{python}` 현재 파이썬, `{root}` 환경 루트, `{pack}` 선택한 문제팩, `{workspace}` 실제 실행할 작업/제출본 복사 경로.
검사 argv는 셸 문자열이 아니라 문자열 배열입니다. 실행기에서 shell=True를 쓰지 않습니다.

## 2. public/ — 응시자에게 주는 자료
MISSION.md, CONTRACT.md, 샘플 데이터, 초기 프로젝트, 지침 4종을 둡니다.
`select`는 이 폴더만 workspace에 복사합니다. private나 reference를 이 안에 넣지 마세요.

현재 공통 사례 실행기는 프로그램에 `--input 임시파일 --json`과 사례 args를 전달합니다.
다른 언어로 구현해도 이 인터페이스를 받는 작은 app.py 어댑터를 제공할 수 있습니다.
다른 형태의 검사가 필요하면 **문제팩 안에 별도 검증 스크립트를 두고 checks의 argv로 연결**하세요. 공통 엔진 수정은 불필요합니다.
다만 현재 validate/self-test의 기본 계약은 public/private JSON 사례와 runner를 요구하므로, 새로운 검증만 쓰려면 최소 연결 사례도 함께 제공해야 합니다.

## 3. private/truth.json — 업무 정답의 단일 출처

```json
{
  "schema_version": 1,
  "persona": "가상 담당자",
  "facts": [{
    "id": "R01",
    "topic": "중복 처리",
    "answer": "정확한 업무 규칙을 수치와 예외까지 적습니다.",
    "keywords": ["중복", "동일", "duplicate"],
    "visibility": "ask",
    "kind": "required"
  }],
  "disclosure": {"overview_ids": ["R01"], "mode": "progressive", "max_new_facts_per_turn": 3},
  "grading_notes": "참조 구현과 다른 구조도 요구사항을 충족하면 허용"
}
```

visibility는 public/overview/ask, kind는 required/context처럼 작성합니다.
public도 실제 공개 문서에 해당 사실을 반드시 써 주세요. 로더가 자연어 문서와의 의미 일치까지 증명하지는 않습니다.
keywords는 **DEMO만** 사용합니다. 실제 llm 모드에서는 모델이 모든 사실과 대화 문맥을 읽어 관련 ID를 선택합니다.

overview_ids에는 첫 업무 소개로 알려줄 내용만 둡니다. 질문할 수 없는 숨은 규칙을 자동 채점 기준에 추가하지 마세요.
담당자에게 코드나 테스트 기대값을 주고 “말하지 마”만 시키지 말고, 업무 사실과 풀이를 파일 단계에서 나누세요.
새 조건이 생기면 truth, 풀이, 사례, 공개 계약이 서로 충돌하지 않는지 갱신하고 version을 올리세요.

## 4. 참조 구현과 독립 기대값
reference/에는 실제 실행 가능한 정답 예시를 둡니다. SOLUTION.md에 수식, 예외, 처리 순서, 대안, 한계를 적습니다.
INTERVIEW_GUIDE.md는 제출물에 맞춰 후속 질문을 만들 때 쓰는 평가자용 참고입니다. 응시자에게 면담 중 답안을 주지 않습니다.

사례 형식:

```json
{
  "schema_version": 1,
  "cases": [{
    "id": "case-01",
    "requirement_ids": ["R01"],
    "input": {"schema_version": 1, "frames": []},
    "args": [],
    "expected_exit": 0,
    "expected": {"items": []},
    "forbidden": ["출력되면 안 되는 합성 민감정보"]
  }]
}
```

input 대신 input_text(손상/BOM), input_hex(비UTF8)를 사용할 수 있습니다. missing_file=true 또는 omit_input=true로 오류 경계를 검사합니다.
expected의 객체 키는 부분 비교, 배열은 길이/순서까지 비교합니다. 숫자는 절대오차 1e-6, bool은 숫자와 다르게 봅니다.
추가 출력 키는 허용됩니다. 민감정보·불필요한 필드 검사는 forbidden과 별도 코드 검토를 함께 사용하세요.
입력 파일은 모든 사례에서 실행 전후 bytes를 비교합니다. 네트워크·파일 접근 전체를 샌드박싱하는 검사는 아닙니다.

**기대값을 독립적으로 계산해 고정하세요.** 채점 중 참조 구현과 응시자 구현의 결과만 비교하면 동일 버그를 정답으로 인정할 위험이 있습니다. 참조 코드는 고정된 기대값을 검증할 대상으로 둡니다.

## 5. 출제자가 확인할 것

```powershell
python exam.py problem validate my-new-problem
python exam.py problem self-test my-new-problem --author
```

validate는 JSON/경로/참조 무결성을 검사하며 임의 문제팩 코드를 실행하지 않습니다. self-test는 참조 프로그램을 실제 실행합니다.
실제 LLM 담당자의 자연어 질문 해석 품질은 별도 질문 리허설로 확인해야 합니다. DEMO가 통과해도 의미 해석 품질이 검증된 것은 아닙니다.
정상 참조의 통과뿐 아니라 경계 판정, 중복 처리 등을 의도적으로 틀린 변형이 실패하는지 확인하세요.

현재 예제에는 공개 4개를 포함하는 최종 48개, 별도 참조 함수 단위 검사, 고장 낸 판정식을 거르는 회귀 검사가 있습니다. 자연어의 모든 의미를 자동 검증하는 시스템은 아닙니다.

## AI에게 새 문제 제작을 맡길 때 전달할 지시

> 이 문서의 문제팩 v1 계약을 따르라. 엔진 파일을 수정하지 말고 problem_packs/<새 ID> 안에서만 새 문제를 작성하라. public에는 모호한 업무 요청과 확정된 I/O 계약만, private에는 완전한 업무 명세·실행 가능한 참조 구현·독립 계산한 기대값·면담 해설을 넣어라. 모든 자동 검사는 질문으로 확인 가능한 requirement_ids에 연결하라. 참조 구현을 유일한 정답 구조로 취급하지 말라. validate와 self-test를 실제 실행하고 실패한 변형도 검출해라. 검증하지 않은 LLM 품질이나 실제 COFA 출제 일치를 주장하지 말라.

## 주의
경로 분리는 권한 경계가 아닙니다. OS 소유자는 정답을 읽을 수 있습니다. import된 문제팩의 검사 명령은 로컬 코드 실행이므로 신뢰한 출제자 자료만 사용하세요. 이미 시작된 회차의 문제팩은 수정하지 말고 새 버전/새 회차를 만드세요.


### 검사 프로세스 시간 제한
`checks` 항목에 선택적으로 `timeout_seconds`(0 초과, 최대 3600)를 지정할 수 있습니다. 기본은 config의 90초입니다. 동봉 문제는 공개 검사 60초, 제출본 전체 검사 300초이며, 개별 앱 실행은 runner의 5초 제한을 적용합니다. 개발 중 검사는 남은 시험 시간을 넘기지 않습니다. 면담 시작 전 준비 검사는 별도 단계입니다.

## v4.1 단계적 공개 계약

`disclosure.mode` 기본값은 `progressive`입니다. 명세 전체를 요청하면 `overview_ids`만, 이미 확인한 조건의 요약에는 실제로 공개한 사실만 반환합니다. 구체적인 복합 질문은 `max_new_facts_per_turn`개의 **신규** 사실을 공개합니다(1~10 정수, 기본 3). 이 숫자는 로컬 연습 설정이지 공식 규칙이 아닙니다.

각 fact에는 그 규칙에 필요한 정의·예외를 함께 적으세요. 사실 하나의 중간을 잘라 전달하지 않습니다. 먼저 특정 코드를 알아맞혀야 다음 조건이 열리는 prerequisite는 없습니다. 관련된 질문으로 모든 필수 조건에 접근할 수 있어야 합니다. 알려지지 않은 조건을 요약에 끼워 넣지 않습니다.

출제자가 자유 학습을 명시적으로 원할 때만 `mode: "open"`을 사용할 수 있습니다. 이 경우 전체 요청에 전체 업무 조건을 제공하며 화면 답변에도 개방 학습 모드라고 표시합니다. 시험 시작 전에 설정하고 문제 버전을 올리세요. 진행 중 변경은 해시 검사에 의해 차단됩니다.

라우터의 자연어 action 선택은 LLM이 담당하고 서버는 선택된 action과 ID에 대한 공개량을 검증합니다. 이는 모든 프롬프트 공격을 막는 보안 보장이 아닙니다. 실제 질문 분류의 타당성은 인증된 LLM으로 별도 점검해야 합니다. DEMO 키워드 검사는 그 타당성의 증명이 아닙니다.


## v5 기본 실전 모드 추가
기존 문서의 자동 확인 문서/담당자 ID 설명은 레거시 모드에 해당합니다. v5 기본은 본인 문장 확인이며, 대화 카드·예측 면담·문제별 품질 사례는 [V5_PROBLEM_EXTENSIONS.md](V5_PROBLEM_EXTENSIONS.md)를 따릅니다.
