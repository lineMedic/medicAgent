# evidence/

이 폴더에는 **실제로 실행한 결과만** 둔다. 설계 문서, 기대값, mock 결과, 사람이 추측한 값은 두지 않는다([AGENTS.md §4.3](../AGENTS.md)).

## 규칙

- 비밀값(토큰·키·비밀번호·수신 주소)을 적지 않는다. 명령 출력에 비밀이 섞였으면 저장하기 전에 가린다.
- 파일마다 실행 시각(UTC `...Z`), 실행 환경(호스트·버전), 실행한 명령, 결과(PASS/FAIL/NOT_RUN)를 적는다.
- 실행하지 않은 항목은 `NOT_RUN`, 확인하지 못한 값은 `미확인` 또는 `null`로 둔다. 0이나 PASS로 채우지 않는다.
- STATUS.md의 `LIVE_VERIFIED` 증거 칸은 이 폴더의 파일 경로를 가리킨다.

## 파일 이름

| 이름 | 내용 | 카드 |
|---|---|---|
| `host-manifest.json` | 데모 호스트에서 만든 `make host-manifest` 출력 | W00 |
| `contest-conditions.md` | 주최 측 답변 원문(확인일·질문·답변·출처·확인자) | W01 |
| `N01-model-tool-call.md` | 모델 tool 선택 → 결과 재입력 → 구조화 제안 스파이크 | W02 |
| `N02-…` ~ `N11-…` | 나머지 런타임·sandbox·GitHub 스파이크 (`N<번호>-<주제>.md`) | W02·W03 |
| `N13-case-search.md` | SQLite FTS5·한국어/오류 토큰 검색 결과(`LINEMEDIC_RECORD_EVIDENCE=1 make test`) | W27 |
| `<카드ID>-<주제>.md` | 카드별 live 검증 기록 (예: `W15-sandbox-s1-run1.md`) | 카드별 |
