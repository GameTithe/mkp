# 실행 및 인수인계

## 실행 명령

Python 3.10 이상과 표준 라이브러리만 필요합니다.

```powershell
python app.py --input data/frames.json --json
python app.py --input data/frames.json --json --scenario 전투 --status regressed
python -m unittest discover -s tests -v
```

성공은 종료코드 0, 실패는 종료코드 2와 stdout의 JSON 오류 객체입니다. `--json`은 계약상 선택이며 생략해도 JSON을 출력합니다.

## 확인한 업무 조건

- 담당자 응답은 제공되지 않았습니다. 확정 조건은 `CONTRACT.md`의 입출력 구조뿐입니다.
- 현재 구현 가정: frame 0~2 제외, `(build, scenario, frame)` 중복의 첫 행 유지, frame time=`max(cpu_ms, gpu_ms)`, 선형 보간 p95, 빌드별 최소 5행.
- 현재 회귀 가정: p95가 2ms 이상이면서 10% 이상 증가(경계 포함). candidate cpu/gpu p95 중 큰 쪽이 병목이며 같으면 balanced.
- 결과는 regressed/stable/insufficient 순서, 같은 상태에서는 변화율 내림차순입니다. 필터는 items에만 적용하고 meta/warnings는 전체 입력 기준입니다.

## 자체 검증 결과

- `python -m unittest discover -s tests -v`: 5개 테스트, 모두 통과.
- 전체 샘플: 종료코드 0. 전투(regressed), 로비(stable), 마을(stable) 순서. meta는 total 74 / accepted 54 / warmup 18 / invalid 1 / duplicate 1.
- 전투+regressed 필터: 종료코드 0, 전투 1개 출력.
- 빈 입력: 종료코드 0, 빈 items/warnings와 0인 meta 출력.
- BOM, 중복, 손상 행, 정확한 회귀 경계, 최소 표본, 잘못된 인수/JSON, 없는 파일, 지나치게 큰 수의 안전한 실패를 자체 테스트로 확인.

## 알려진 한계

- PLAN.md의 업무 질문이 미확인이라 집계·판정 값은 담당자 확인 전 가정입니다.
- `../exam.py check`와 별도 인수 검사는 workspace 밖 접근 제한 때문에 실행하지 않았습니다.
- 대용량 성능과 처리량은 측정하지 않았습니다.
