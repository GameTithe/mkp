# v5 변경 기록

사용자 요청: 현업 담당자가 정답을 쉽게 내놓지 않으면서도 정당한 질문에는 답할 것, 평가의 근거를 검증할 것, 모든 시험 단계 종료 후 피드백을 제공할 것.

| 제안 | 구현 위치 | 의미/한계 |
|---|---|---|
| 업무 맥락 중심 담당자 | cofa/contextual.py, truth.dialogue | LLM 분류 + 출제자 문구. 자유로운 인간 대화의 완전 모사 아님 |
| 응시자 이해 확인 | contextual.confirm, requirements CLI/UI | 확인·정정·미확정, Q... 문장 ID. 의미 판정은 LLM 오류 가능 |
| 요구사항→작업 연결 | contextual.link, feedback.trace_requirements | 실제 E... 기록만 연결. 자기신고와 실행 결과는 구별 |
| 평가 근거 의미 재검토 | cofa/quality.py | 정확한 인용 검사 + 별도 LLM 검토. 사람 교정 아님 |
| 반복 채점 | evaluation.Evaluator.run | 기본 2회, 불일치를 평균으로 숨기지 않고 보류 |
| 실행 예측 면담 | cofa/prediction.py, interview_probes.json | 먼저 답변, 고정 제출본 복사 실행, 비교 질문. 자동 정답 노출 아님 |
| 기준 사례 품질 검사 | cofa/benchmarks.py | 12채점+12담당자. 실제 live 호출은 별도 사용량. 미교정 사례임을 명시 |
| 종료 피드백 | cofa/feedback.py | 평가 후 자동. 실패 시 사실 리포트 보존. 점수 불변 |
| 종료 후 코치 | feedback.chat, UI 복습 코치 | 자유 설명은 학습 조언이며 새 실행 검증 아님 |

기존 v4.1의 자동 `REQUIREMENTS_CONFIRMED.md` 대신 v5 기본 모드는 본인이 제출한 문장만 `REQUIREMENTS_REVIEW.md`로 관리합니다. 기존 담당자 대화 자체는 여전히 메인 Codex의 작업 문맥에 전달합니다.

기존 테스트는 레거시 호환 설정으로 유지하고, 새로운 기본값은 v5 전용 테스트에서 별도로 확인합니다. 테스트 통과 숫자는 의미 판단 정확성을 인증하는 수치가 아닙니다.

배점은 기술·의도·인지 3축 각 3개 항목 0~4, 축 동등 가중치인 연습 설정 그대로입니다. 실제 공식 배점/합격선은 구현하지 않았습니다.
