# ENV

훈련 제한: 개발 120분 + 제출 후 면담 30분. 로컬 Python 3.10 이상, 로그인된 Codex CLI.
모든 AI 대화는 콘솔/기록형 CLI 경유. 직접 실행 명령은 콘솔 또는 `exam.py run -- ...` 경유.

## 실제 구현 환경

- Windows PowerShell, Python 3.12.3(실제 `python --version` 확인).
- Python 표준 라이브러리만 사용. 설치 작업 없음.
- 실행: `python app.py --input data/frames.json --json`
- 테스트: `python -m unittest discover -s tests -v`
- 공개 샘플: `data/frames.json`, `data/empty.json`

## 재현 명령

```powershell
python app.py --input data/frames.json --json
python app.py --input data/frames.json --json --scenario 전투 --status regressed
python app.py --input data/empty.json --json
python -m unittest discover -s tests -v
```

## 제약과 보안

- 외부 API와 네트워크를 사용하지 않음.
- 입력의 추가 필드는 읽거나 출력하지 않으며 오류에 입력 원문·개인 경로·traceback을 포함하지 않음.
- `../exam.py check`는 현재 workspace 밖 접근 금지 때문에 실행하지 않음.
