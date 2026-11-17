# 출처 기록

## v4.1 추가 검토
2026-11-16에 사용자가 지정한 영상의 공개 문답을 검토했습니다. 원본 대본 추출은 성공하지 못했습니다. 이번 반영의 출처와 조회 범위는 [PODCAST_SOURCE_NOTES.md](PODCAST_SOURCE_NOTES.md)에 따로 구분했습니다. 아래는 v4에 포함됐던 역사적 조사 기록이며, 아래 모든 링크를 이번에 재검증했다는 뜻은 아닙니다.

---

# 공개 근거와 모의환경 설계의 구분

확인일: 2026-11-15. 이 도구는 비공식 연습용이며 실제 시험 문제·내부 프롬프트·배점의 복제본이 아닙니다.

## 1. Cofa 공식 Probe 설명
https://www.getcofa.com/ko/probe

공식 페이지에서 확인되는 내용: 직무별 실무 과제, 코드·문서·테스트가 있는 IDE, 프롬프트/도구 설정/AI 대화/검증 기록 수집, 제출 결과와 판단 근거를 설명하는 사후 Q&A, 코드·로그·답변 근거와 연결된 평가 리포트, 기술·의도·인지 3축.
이 자료만으로 실제 client 시험 문제, 시간, 모델, 정답 노출 방식, 항목별 배점을 알 수는 없습니다.

## 2. SK하이닉스 AI 해커톤 2차 예선 참가자 회고
https://kr.linkedin.com/posts/jaehyork_limjhyeok-ai-engineer-blog-activity-7525087449220403200-v-XQ

작성자가 직접 참여했다고 설명한 LinkedIn 회고를 읽었습니다. 내용: 2차 예선에서 Cofa Probe 사용, 가상 현업 담당자와 대화하여 요구 도출, Codex로 3시간 구현, 이후 AI 없이 자신의 설계 근거·trade-off·취약점을 설명하는 30분 시험.
이는 참가자 1인의 경험담이지 모든 SK/크래프톤 시험의 공식 규칙이 아닙니다. 1차 예선이나 SK그룹 임직원 대상 별도 행사와 혼동하지 않았습니다. 연결된 개인 블로그 원문은 접근하지 못했으므로 그 내용을 근거로 쓰지 않았습니다.
실제 현업 담당자의 내부 프롬프트나 정답 접근 범위는 공개 확인하지 못했습니다.

## 3. Codex 공식 연결 문서
https://developers.openai.com/codex/noninteractive/
현재 문서 경로: https://learn.chatgpt.com/docs/non-interactive-mode
https://developers.openai.com/codex/cli/reference/
https://developers.openai.com/blog/eval-skills

`codex exec --json`, 세션 재개, 구조화 출력 등의 연결부를 구현하는 근거로 사용했습니다. 실제 사용자 계정 인증·현재 설치 버전과의 동작은 doctor --live로 확인해야 합니다.

## 4. 사용자 제공 조건
Client 직군, Codex, 구현 120분 + 제출 후 30분 AI Q&A. 첨부 평가 이미지의 기술·의도·인지 3축 및 각 3개 항목.
SK 참가자 회고의 3시간을 사용자의 120분으로 바꾼 것은 명시적 요청을 따른 연습 설정이지 공식 시간이 같다고 판단한 것이 아닙니다.
면담 질문은 AI가 생성하지만 답변은 응시자가 직접 작성합니다. 답변을 대신 만드는 메인 코딩 AI는 제출 후 차단합니다.

## 5. 독립적으로 설계한 부분
FrameScope 문제, 입력 형식·판정 규칙·수치·테스트·참조 코드·가상 인물은 전부 창작입니다. 게임 클라이언트 성능 로그와 관련된 연습 과제일 뿐 실제 client 출제 범위를 예측하거나 복원하지 않습니다.
업무 명세 전체를 담당자에게 제공하고 관련 사실 ID만 골라 정해진 원문으로 답하게 하는 방식은 정확성과 재현성을 위한 모의환경 설계입니다. 실제 Cofa가 같은 구현을 쓴다는 근거는 없습니다.
문제팩 구조·48개 인수 사례·0~4점 하위 기준·축별 동등 가중치·정답 공개 시점 역시 모의환경의 설계 선택입니다.


## v5 재확인 (2026-11-16)
- Cofa 공식 Probe: https://www.getcofa.com/ko/probe — 산출물/대화/검증 기록 수집, 사후 설명과 구현 대조, 기술·의도·인지 근거 리포트.
- OpenAI Codex non-interactive 문서: https://developers.openai.com/codex/noninteractive/ (https://learn.chatgpt.com/docs/non-interactive-mode로 이동) — exec --json, -o, --output-schema, 저장된 CLI 인증.

v5의 대화 카드·응시자 문장 확인·반복 2회·구체적인 보류 규칙·실행 예측 입력·복습 코치는 이 모의환경의 설계이며 공식 채점 내부 구현을 확인한 것이 아닙니다. 기존 팟캐스트 원본 전사 실패의 범위는 PODCAST_SOURCE_NOTES.md 그대로이며, 이번에 영상 전체를 확인했다는 뜻이 아닙니다.
