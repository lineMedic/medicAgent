# N13 사례 검색: SQLite FTS5·한국어/오류 토큰·ACL/snapshot 필터

- 실행 시각(UTC): 2026-09-27T16:56:57.000729Z
- 실행 환경: 로컬 개발 Mac(macOS-26.6.2-arm64-arm-64bit), Python SQLite 3.46.0. 데모 호스트(G1) 아님
- 명령: `LINEMEDIC_RECORD_EVIDENCE=1 make test` (`linemedic/tests/integration/test_case_memory.py`)
- 엔진: `sqlite_fts5`, tokenizer `unicode61`, 질의 정규화 `d54-v1`, snapshot `MEM-47C8BD30C381`(합성 노트 1개)
- 결과: PASS (FTS5 사용 가능, 아래 질의가 모두 오류 없이 실행됨)

| 질의 | status | 결과(note ID(match)) |
|---|---|---|
| `KeyError` | OK | CASE-394E0C661E37-R2(keyword) |
| `inspector_id` | OK | CASE-394E0C661E37-R2(keyword) |
| `미지정` | OK | CASE-394E0C661E37-R2(keyword) |
| `미지정으로` | NO_HIT | - |
| `검사자 미지정` | OK | CASE-394E0C661E37-R2(keyword) |

- ACL·snapshot 필터: 다른 repo·다른 서비스·cutoff 뒤·현재 run·평가 식별자 노트가 결과에 나오지 않는 것은 같은 파일의 T-MEM-03 테스트가 확인한다
- 한계: 합성 노트 소수로 토큰 일치 여부만 봤다. unicode61은 한국어 형태소·조사를 처리하지 않는다(`미지정으로` ≠ `미지정`). 검색 품질·유사 사례 정확도·RAG 효과는 측정하지 않았다
