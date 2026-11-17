# 참조 구현 실행

```sh
python app.py --input ../../public/data/frames.json --json
python -m unittest test_reference -v
```

요구사항은 문제팩 private/truth.json R01~R11에 대응합니다.
app.py는 인자/파일/JSON 오류 경계를 담당하고 framescope.py는 순수 계산을 담당합니다.
최종 인수 검사는 `python exam.py problem self-test client-frame-regression --author`로 실행합니다.

한계: 합성 로그의 계약 분석일 뿐 실제 GPU/CPU 인과관계와 프레임 전달 지연을 측정하지 않습니다.
전체 입력과 정렬 배열을 메모리에 보관합니다. 대규모 스트리밍, 실시간 게임 연결, GUI는 구현하지 않았습니다.
자동 검사만으로 보안·유지보수성·응시자의 이해를 판정하지 않습니다.
