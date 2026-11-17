# 시작 안내 — v5 / Client / Codex / 120 + 30분

## 새 폴더에서 시작
기존 v4/v4.1 폴더에 덮어쓰지 마세요. ZIP을 풀고 `exam.py`가 있는 `cofa_v5` 폴더에서 PowerShell을 엽니다.

```powershell
# 설치·로그인이 이미 되어 있으면 처음 두 줄 생략
npm install -g @openai/codex
codex login
python exam.py doctor --live
python exam.py problem validate
python exam.py serve --open
```

Python 3.10 이상과 Node.js/npm이 필요합니다. `python` 대신 `py -3`을 사용할 수 있습니다. `doctor --live`는 실제 계정을 호출하므로 사용량이 발생하나 타이머는 시작하지 않습니다. 오류가 있으면 시험 시작 전에 해결하세요. 모델 연결/Windows 실제 실행은 이 배포 환경에서 실측하지 못했습니다.

## 화면에서 진행

**실제 Codex LLM → 120분 시작 → 현업 담당자 → 요구사항 확인 → 메인 Codex/테스트 → 구현 제출 → 30분 면담 → 면담 종료 → 최종 평가 + 피드백**.

담당자에게 업무·판정·예외를 구체적으로 물으세요. “정답 전부” 요청에는 업무 상황만 설명합니다. 구체적인 질문은 바로 답할 수 있고, 억지로 여러 단계를 거칠 필요는 없습니다.

`요구사항 확인` 탭에는 본인이 이해한 조건을 **한 줄에 한 문장씩** 적습니다. 결과는 `REQUIREMENTS_REVIEW.md`에 쌓입니다. 이 문서는 전체 정답집이 아닙니다. 미확정/확인 유보는 동의나 승인으로 해석하지 마세요.

구현은 같은 화면의 `메인 Codex` 탭에서 진행하세요. 외부 raw Codex/ChatGPT 대화는 기록되지 않습니다. 서버 터미널은 계속 켜두세요.

면담에는 본인이 직접 답합니다. 실행 예측 질문에서는 실행 전 예상 결과와 이유를 먼저 답하고, 다음에 실제 제출본 결과가 보이면 차이를 설명하세요. 참조 정답을 실행해 보여주는 것이 아닙니다.

## 모든 검사·면담이 끝난 뒤 피드백

`최종 평가 + 피드백` 완료 시 피드백이 화면 아래에 열립니다. 일부 테스트 실패나 점수 보류가 있어도 피드백은 가능합니다. 반드시 모든 검사를 통과해야 하는 조건은 아닙니다.

`종료 피드백 보기`: 실제 검사 결과, 요구사항 확인·반영 흐름, 유지할 행동, 개선할 행동, 다시 물어볼 질문, 다음 연습과 통과 조건.

`복습 코치`: 종료 후 질문. “왜 보류됐나요?”, “어떤 질문을 더 했어야 하나요?”, “다음 연습에서 무엇을 검증해야 하나요?”처럼 물을 수 있습니다. 이 대화는 점수를 바꾸지 않습니다.

모델 호출이 실패하면 없는 평가를 만들지 않고 사실만 표시합니다. 연결을 확인하고 `피드백 재생성`을 누르거나 아래 명령으로 재시도하세요.

```powershell
python exam.py feedback --retry
```

파일은 `reports/feedback.html`, `feedback.md`, `feedback.json`. 평가 근거는 `reports/final_report.html`입니다. 전체 풀이를 확인하려면 평가 후 `정답·해설 공개` 또는 `python exam.py problem debrief`를 사용하세요.

## 다시 연습

```powershell
python exam.py problem new-session client-frame-regression "../practice_02"
```

새 폴더의 exam.py를 실행합니다. 기존 기록은 유지합니다.

**DEMO 주의:** 연결 확인용이며 실제 구현·점수·강점/약점 판단을 하지 않습니다. 요구사항 문장도 의미적으로 승인하지 않습니다.

**정답 보호 주의:** 연습 도중 `problem_packs/*/private`, `logs/calls`, `analysis`, `quality_runs`를 읽지 마세요. OS 수준의 접근 차단은 아닙니다.
