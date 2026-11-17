# 시작 안내 — v4.1 / Client / Codex / 120 + 30분

## 1. 새 폴더에 압축 해제
기존 v3/v4 폴더에 덮어쓰지 마세요. 기존 응시 기록은 기존 폴더에 보관합니다.
`exam.py`가 있는 **cofa_v4_1** 폴더에서 PowerShell을 엽니다.

## 2. 사전 연결 검사 (타이머 시작 안 됨)
Python 3.10 이상과 Node.js/npm이 필요합니다. Codex 설치·로그인이 되어 있다면 첫 두 명령은 생략합니다.

```powershell
npm install -g @openai/codex
codex login
python exam.py doctor --live
python exam.py problem validate
```

`python` 대신 `py -3`을 써도 됩니다. `doctor --live`는 실제 계정/네트워크를 사용하므로 사용량이 발생할 수 있습니다. 실제 Codex 실행이 실패하면 아직 시험을 시작하지 말고 출력 오류를 확인하세요.

## 선택: 사전 지침 반입 (시험 시작 전)
평소 사용하던 텍스트 지침 폴더만 넣습니다. 실행 파일이나 인증정보는 포함하지 마세요.

```powershell
python exam.py prep import "C:\my-codex-kit" --label my-kit --origin "본인 작성" --purpose "작업 분할과 테스트 지침"
```

반입하지 않아도 시험을 진행할 수 있습니다. 실제 시험 반입 허용 여부는 해당 전형 안내를 따르세요.

## 3. 브라우저 열기

```powershell
python exam.py serve --open
```

또는 `launch_windows.bat`을 실행합니다. 서버 터미널을 닫지 마세요. 타이머 자동 마감은 서버가 켜져 있을 때만 동작합니다.

**FrameScope — 클라이언트 성능 회귀 분석** 문제는 이미 선택되어 있습니다.
화면 위 문제팩 제목을 확인하고 `실제 Codex LLM` → `120분 시작`을 누릅니다.
`MISSION.md`를 읽고 **현업 담당자** 탭에 직접 질문하세요.

예시: “누가 어떤 상황에서 사용할 도구인가요?”
v4.1 담당자는 명세 전체 요청에 정답을 쏟아내지 않습니다. 현재 업무·판단·예외를 구체적으로 물어보세요. 기초적인 질문도 가능합니다.
담당자 답변 중 확인된 조건은 `workspace/REQUIREMENTS_CONFIRMED.md`에 자동 기록됩니다.

개발 도구는 같은 화면의 **메인 Codex** 탭입니다. 별도의 raw `codex` 터미널로 개발하면 대화 수집을 보장하지 않습니다.

## 4. 제출과 면담
담당자 질의 → Plan/Env/Agents/Handoff 갱신 → 구현/병렬 작업/테스트 → 제출 → 30분 면담 → 최종 평가.
공개 테스트 통과만으로 완료가 아닙니다. 최종 제출본에는 문제팩의 별도 인수 검사가 실행됩니다.
면담에는 응시자 본인이 답합니다. 메인 코딩 AI와 담당자에게 개발 지원을 받는 기능은 제출 후 차단됩니다.

## 5. 정답 확인은 평가 후
화면의 **정답·해설 공개** 또는 다음 명령을 사용합니다.

```powershell
python exam.py problem debrief
```

`reports/debrief/`에 전체 업무 정답·참조 구현·기대값·면담 해설이 복사됩니다.
연습 중에는 `problem_packs/*/private`, `logs/calls`, `analysis`의 내부 자료를 열지 마세요.
로컬 소유자가 파일을 읽는 것을 OS 수준으로 막는 프로그램은 아닙니다.

## 다음 문제 / 새 회차
같은 문제로 다시 연습:

```powershell
python exam.py problem new-session client-frame-regression "../practice_02"
```

새 문제팩 폴더를 `problem_packs/`에 넣은 후:

```powershell
python exam.py problem list
python exam.py problem validate my-new-problem
python exam.py problem new-session my-new-problem "../practice_new"
```

출제 방법은 `docs/PROBLEM_PACK_AUTHORING.md`에 있습니다. 출제자용 참조 구현 검증 명령은 정답을 실행하므로, 처음 응시할 때는 실행하지 마세요.

**DEMO는 LLM 없는 연결 점검 모드입니다. 담당자는 키워드 기반, 메인 AI는 실제 구현을 하지 않으며, 실제 역량 점수를 생성하지 않습니다.**

## 이번 수정의 근거와 사용량
원본 대본을 추출한 것은 아닙니다. 진행자의 공개 문답에서 확인한 부분만 반영했으며 `docs/PODCAST_SOURCE_NOTES.md`에 한계를 적었습니다.
화면의 **개발 / 환경 운영 사용량**을 새로고침하거나 `python exam.py usage`로 확인합니다. 금액/미보고 사용량을 추정하지 않습니다. 최종 리포트에는 `reports/resources.json`도 생성됩니다.
