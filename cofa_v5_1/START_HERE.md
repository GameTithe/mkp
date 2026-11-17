# 시작 안내 — v5.1 / Client / Codex / 개발 120분 + 면담 30분

## 새 회차로 시작
v4.1 또는 v5에 덮어쓰지 마세요. 기존 기록은 그대로 두고 ZIP을 새 폴더에 풉니다.
`exam.py`가 있는 **cofa_v5_1** 폴더에서 PowerShell을 엽니다.

Python 3.10 이상, Node.js/npm, 설치·로그인된 Codex CLI가 필요합니다. Python 외부 의존성은 없습니다.

```powershell
# 기존 Codex 설치·로그인이 되어 있다면 처음 두 줄 생략
npm install -g @openai/codex
codex login
python exam.py doctor --live
python exam.py problem validate
python exam.py serve --open
```

`doctor --live`는 실제 계정을 사용하므로 사용량이 발생할 수 있지만 시험 타이머는 시작하지 않습니다. 이 배포 환경에서 실제 Codex 인증/Windows를 검사한 것은 아닙니다.

## 시험 진행
실제 Codex LLM 선택 → 120분 시작 → 담당자 질문·요구사항 확인 → 메인 Codex/테스트 → 구현 제출 → 30분 본인 면담 → 면담 종료 → 최종 평가 + 피드백.

담당자는 전체 정답 문서를 대필하지 않되, 구체적으로 물은 업무 규칙은 답합니다. 질문의 의미를 잘못 이해한 것 같으면 **담당자 질문 재검토**를 누르세요. 마지막 원래 질문을 당시 문맥으로 성공 1회 재검토합니다. 힌트·공개 범위를 늘리는 기능은 아닙니다. 최초 답변·재검토는 모두 보존됩니다.

CLI는 `python exam.py ask-review`이며 특정 과거 질문에는 실제 E... ID를 붙입니다. 다른 질문을 새로 전달하려면 일반 담당자 채팅을 사용합니다.

요구사항 확인 탭에는 이해한 조건을 한 줄 한 문장씩 적습니다. 확인 유보는 승인이 아닙니다. 구현은 화면의 **Codex CLI 열기**로 띄운 실제 Codex TUI를 이용합니다. 이 창의 기록은 사이트와 동기화되지만 다른 ChatGPT/Codex 세션은 수집되지 않습니다.

서버 터미널을 계속 켜두세요. 서버가 꺼지면 자동 마감은 실행되지 않습니다. 면담 중에는 본인이 직접 설명·예측하고 실제 제출본 실행과 대조합니다.

## 시험 종료와 채점 완료는 다릅니다
면담 종료 시 **사실 피드백**은 자동 생성됩니다. 테스트가 실패했거나 채점이 아직 안 되어도 볼 수 있습니다.

- **채점 전/채점 실패:** 실제 테스트 결과, 제출 시각, 면담 질문·답변, 실패 사유만 표시. 점수·강점·약점을 임의 생성하지 않습니다.
- **채점 완료:** 확정 점수와 항목별 미확정 사유, 유지할 행동·개선할 행동·다음 연습을 확인합니다.
- **제출본/로그 무결성 오류:** 정상 기록처럼 보여주지 않고 별도 경고 파일로 차단합니다.

채점 실패 후에는 시험을 다시 보지 말고 같은 회차에서 **최종 평가 + 피드백**을 다시 누르세요.

```powershell
python exam.py feedback          # 채점 전/실패 상태에도 사실 확인 가능
python exam.py evaluate          # 실패한 채점 재시도 (실제 계정 사용량 발생 가능)
python exam.py feedback --retry  # 채점 완료 후 피드백 생성만 다시 시도
```

피드백 파일: `reports/feedback.html`, `.md`, `.json`. 채점 전/실패 사실: `reports/exam_facts.json`.
무결성 오류 경고: `reports/feedback_blocked.json`.
`reports/final_report.html`은 마지막 성공한 평가 파일일 수 있습니다. 현재 채점 상태는 화면/`exam.py status`를 확인하세요. 재시도가 실패했으면 이전 성공 점수를 현재 결과로 해석하지 마세요.

복습 코치는 채점이 끝난 후 능력 해석을 제공합니다. 채점 전에는 사실 확인·재시도 안내만 하며, 종료 후 대화는 점수를 바꾸지 않습니다.

## 선택: 실제 담당자·채점 품질 리허설
```powershell
python exam.py quality validate
python exam.py quality live --scope stakeholder --repeats 2
python exam.py quality live --scope judge --repeats 2
```
첫 명령은 형식 검사만 합니다. live 명령은 실제 계정 호출이며, 담당자 24사례를 2회면 담당자 호출 48회입니다. 채점 12사례는 반복마다 1차 채점과 근거 검토가 추가됩니다. 사용량·시간을 고려해 한 종류씩 실행하세요.
사례는 사람 교정 전 출제자 작성 자료입니다. 원문/결과에 정답이 포함될 수 있으므로 첫 응시 전 응시자가 읽으면 연습이 오염될 수 있습니다. 결과는 `quality_runs/`에 저장되며 실제 평가 정확성 인증이 아닙니다.

## 반복 연습
```powershell
python exam.py problem new-session client-frame-regression "../practice_02"
```
새 폴더에서 시작합니다. 기존 세션 기록은 보존됩니다. 새 문제는 기존 문제팩 계약으로 연결합니다.

DEMO는 연결 점검용이고 실제 구현·역량 점수를 만들지 않습니다. 원본/정답/내부 로그를 OS 수준에서 감추는 제품이 아니므로 `problem_packs/*/private`, `logs/calls`, `analysis`, `quality_runs`를 시험 중 열지 마세요.
