# 입출력 연결 계약 (공개)

이 문서는 테스트 연결 모양을 고정합니다. 업무 기준은 담당자에게 질문하세요.

```sh
python app.py --input data/frames.json --json
python app.py --input data/frames.json --json --scenario 전투 --status regressed
```

필수 입력 옵션 `--input PATH`, 선택 `--json`(생략해도 JSON), `--scenario NAME`, `--status regressed|stable|insufficient`.
성공: 종료코드 0, stdout에 JSON 객체 하나, stderr는 비어 있거나 민감정보 없는 진단만. 실패: 종료코드 2, stdout에 `{"error":{"code":"INVALID_INPUT|IO_ERROR|INVALID_ARGS","message":"설명"}}`.
메시지 문자열은 자유입니다. traceback·입력 원문·개인 경로를 출력하지 마세요.

입력은 UTF-8(BOM 허용) JSON: `{"schema_version":1,"frames":[{"build":"baseline","scenario":"전투","frame":3,"cpu_ms":8.0,"gpu_ms":11.0}]}`.
프레임에는 build, scenario, frame, cpu_ms, gpu_ms가 있고 추가 필드가 있을 수 있습니다. 상세 유효성/제외/집계 기준은 담당자에게 확인합니다.

출력의 **구조 예시**(실제 정답 수치가 아님):
```json
{
  "items": [{
    "scenario": "전투",
    "baseline_count": 5,
    "candidate_count": 5,
    "baseline_p95_ms": 10,
    "candidate_p95_ms": 13,
    "delta_ms": 3,
    "delta_percent": 30,
    "status": "regressed",
    "bottleneck": "gpu"
  }],
  "meta": {"total_rows":10,"accepted_frames":10,"ignored_warmup":0,"invalid_rows":0,"duplicate_rows":0},
  "warnings": []
}
```

items 필드는 위 이름 모두 포함. `status`는 regressed/stable/insufficient, `bottleneck`은 cpu/gpu/balanced/unknown.
통계가 없는 수치 필드는 null, 개수는 0. 경고는 `{"code":"INVALID_ROW|DUPLICATE_FRAME","row":1,"fields":["cpu_ms"]}`.
JSON 키 순서는 무관하지만 items/warnings의 순서는 의미가 있습니다. 필드 확장은 가능하되 민감정보를 포함하면 안 됩니다.
수치 비교는 절대오차 1e-6. **판정 경계는 정확**해야 하며 계산 도중 반올림하지 마세요.

필수 납품: app.py(다른 언어 어댑터 가능), RUNBOOK.md(실행/테스트/한계), 자체 테스트.
`python ../exam.py check`는 공개 연결 검사입니다. 통과해도 업무 로직이 맞다는 뜻은 아닙니다. 최종 제출본에는 별도 인수 검사가 실행됩니다.
