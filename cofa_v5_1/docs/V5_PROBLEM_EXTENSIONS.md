# v5 문제팩 확장 (기존 v1 연결 규격 호환)

기존 `problem.json`, public, truth, reference, suites/checks 규격을 유지합니다. 아래 선택 필드를 쓰면 새 기능이 문제에 맞춰 교체됩니다. 동봉 문제는 v5.1에서 2.0.1입니다.

## 업무 대화 카드
`private/truth.json`에 `dialogue` 객체를 추가합니다. 키는 `overview`, `workflow`, `pain`, `priority`, `clarify`이며 각 값은 문자열입니다. 작업 상황·불편을 사실에 맞게 설명하되 구체적인 구현·전체 체크리스트를 대필하지 마세요. 수치/예외의 정확한 답은 기존 `facts[].answer`에 유지합니다. 정확히 질문한 조건을 마술 키워드 뒤로 숨기지 마세요.

답변 내용이 업무 명세와 의미적으로 일치하는지는 출제자가 확인해야 합니다. 로더는 자연어의 정합성을 증명하지 않습니다. 카드가 없으면 업무 개요 사실을 사용하므로 대화 난이도가 덜 사실적일 수 있습니다.

## 실행 예측 입력 (선택)
`problem.json`에 `"interview_probes": "private/interview_probes.json"`을 추가합니다.

```json
{"probes":[{"id":"boundary","question":"이 입력에서 제출 코드의 출력과 이유를 예측하세요.","input":{"schema_version":1,"frames":[]},"args":[]}]}
```

필드는 위 4개만 허용합니다. expected/answer를 넣지 마세요. args로 --input/--json 경로를 바꾸지 못합니다. 기존 runner 계약으로만 제출본 복사본을 실행하며 LLM이 만든 임의 명령은 실행하지 않습니다. runner의 timeout과 남은 면담 시간을 적용합니다. 동봉은 2개이며 본인 답변 수 1회/4회 뒤 아직 쓰지 않은 probe를 제공합니다. 문제에 맞는 작은 입력을 작성하고, 면담 시간이 부족하면 실행되지 않을 수 있음을 고려하세요.

## 담당자 품질 사례 (선택)
`problem.json`에 `"stakeholder_benchmark": "private/quality_routes.json"`을 추가합니다. 예시:

```json
{"cases":[{"id":"specific-rule","question":"정확한 기준이 무엇인가요?","expected_intents":["rule"],"must_include":["R01"],"must_exclude":[]}]}
```

id는 공통 채점 사례 ID와 겹치지 않아야 합니다. 단독 명세/입력 경계/동의 유도/정답 코드 요구/요약에서 미공개 유출 여부를 포함하세요. `history` 및 `known_fact_ids`를 선택적으로 지정할 수 있습니다. 실제 모델에 대한 기대는 출제자 판단이며 별도 사람 검토 없이는 사람 평가 정확도로 표시하지 마세요.

공통 12개 채점 검증 사례는 `quality/benchmarks.json`에 있습니다. 담당자 사례만 문제별 파일로 분리되므로 새로운 문제로 교체해도 FrameScope의 R... 참조가 끼어들지 않습니다.

## 확인
```powershell
python exam.py problem validate YOUR_ID
python exam.py problem self-test YOUR_ID --author
python exam.py quality validate
# 실제 모델 호출, 사용량 발생. 미교정 기준 사례와 비교하는 도구:
python exam.py quality live --repeats 2
```

self-test는 정답을 실행하며 live 검사 결과에는 비공개 조건이 담길 수 있습니다. 응시 도중 열지 마세요.

## v5.1 보충
quality live의 `--scope stakeholder`로 담당자만 반복 검사할 수 있습니다. 사례에 선택 문자열 `group`을 붙여 paraphrase/followup/false_premise 등의 유형을 구별할 수 있습니다. raw_match와 최종 reviewed_match를 구별하며, 재검토 오류/보류는 통과가 아닙니다. 사례의 human_reviewed=false를 자동으로 바꾸지 마세요.
