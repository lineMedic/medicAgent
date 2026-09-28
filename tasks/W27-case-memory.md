# W27 — 사례 기억: case builder·outcome·FTS5 검색·snapshot

| 항목 | 값 |
|---|---|
| 등급 | core |
| 자율성 | A |
| 선행 | W05, W06, W07 |
| 목표 상태 | UNIT_TESTED |
| 원본 근거 | [spec 17 전체](../spec/docs/17-case-memory.md), [spec 04 §5.1·§6](../spec/docs/04-data-state.md), [spec templates/case-note.md](../spec/templates/case-note.md), [spec 14 W20](../spec/docs/14-decisions-sources.md#w20) |
| 참조 | [docs/03 §4·§6](../docs/03-domain-model.md), [docs/04 §5](../docs/04-api-reference.md), [docs/05 사례 기억 흐름](../docs/05-workflows.md), DECISIONS D54 |
| 요구·테스트 | FR-22, FR-23, FR-26, INV-09, INV-14 / T-MEM-01~05, N13 |

## 목표

host가 원본 이벤트에서 검증 수준별 case note를 만들고(PR-only는 UNVERIFIED, 권한 차단은 BLOCKED), memory_assisted 모드에서만 고정 snapshot 안의 사례를 exact + FTS5로 찾아 현재 incident의 history projection evidence로 돌려준다. cold_start는 사례를 주지 않는다.

## 만들 파일

- `linemedic/control_plane/migrations/0002_case_search_fts5.sql` — `CREATE VIRTUAL TABLE case_search USING fts5(note_id UNINDEXED, search_text, tokenize='unicode61')`. FTS5가 없는 환경이면 migration을 건너뛰고 엔진을 `keyword_fallback`으로 기록
- `linemedic/control_plane/memory/builder.py`
  - 입력 이벤트: proposal 검사 결과, PR_READY, HANDOFF_DRAFTED, WORK_BLOCKED, verification 최종 결과. `source_event_key`로 한 번만 처리
  - outcome 규칙([docs/03 §6](../docs/03-domain-model.md)). `VERIFIED_SUCCESS`는 verification PASS + target SHA/image/contract hash를 도메인 검사로 확인한 경우만. `origin=operator_note`·PR-only로 우회 불가
  - 사실 필드는 DB 원본에서만(요약 LLM 없음, 고정 템플릿). 필드: [spec 17 §3](../spec/docs/17-case-memory.md) 구조(`schema_version: linemedic.case.v4`)
  - 정제(비밀·개인정보·자유 입력 위험 내용) 실패 시 `DRAFT`로 남김. 성공 시 `PUBLISHED` + FTS insert를 **같은 트랜잭션**에서
  - 후속 결과는 같은 series의 새 revision(`supersedes_id`). 잘못된 자료는 `RETRACTED` + 이유(삭제 없음)
  - S1b는 `origin=human_injected_negative`로 저장, agent 성능 cohort와 분리. seed 사례는 `seed=true`
- `linemedic/control_plane/memory/snapshot.py` — `make memory-snapshot RUN_ID=`: 허용 note ID·revision·content hash·created_at·cutoff·scope·선택 기준·seed 여부를 불변 manifest(`eval/snapshots/MEM-*.json`)로 저장. 현재 run의 결과·미래 revision·holdout은 넣지 않는다. G9: `LIST=1`로 후보만 출력하고 사람이 고른 note ID(`NOTES=`·`NOTES_FILE=`)만 넣는다(사람 제안·S1b 노트는 고른 경우에만, D84 ⑤)
- `linemedic/control_plane/memory/search.py`
  - mode `cold_start` → `DISABLED`, 결과 없음
  - mode `memory_assisted` → snapshot의 정확한 revision만 대상. ACL·repo·service·publish·snapshot membership 필터를 **top_k 선택 전에** SQL로 적용 → exact `problem_fingerprint` 조회 + FTS5 BM25(질의는 D54 토큰화·따옴표·OR·파라미터 바인딩) → outcome별 후보 병합, 관련 실패/차단 사례가 있으면 최소 1건 포함(무관한 실패를 억지로 넣지 않음) → top_k ≤ 5, snippet ≤ 2,000자 → source/contract가 현재와 다르면 `applicability_warning`
  - RETRACTED는 snapshot에 있어도 제외하고 "입력 집합 변경"을 결과에 기록
  - DB·인덱스 오류 → `UNAVAILABLE`(NO_HIT로 바꾸지 않음)
  - 모든 검색을 `case_retrievals`에 기록(mode, snapshot_id, engine, status, query, results)
- `linemedic/control_plane/memory/projection.py` — 현재 incident에 새 evidence(`kind=history_projection`, 원본 note ID·revision·hash·source event 포함)를 만들어 그 ID를 반환
- `tools_api.py` 추가 — `search_cases`(`q` 선택, `limit` 1~5)
- `ops_api.py` 추가 — `POST /ops/cases/rebuild-index`(PUBLISHED revision 전부 재색인, outcome 불변), `GET /ops/cases/{note_id}`
- CLI·Makefile — `make rebuild-case-index`, `make memory-snapshot RUN_ID=`
- 테스트: `integration/test_case_memory.py`, DDL 테스트에 FTS5 항목 추가

## 수용 기준

- T-MEM-01: verifier PASS → VERIFIED_SUCCESS, 업무 FAIL → VERIFIED_FAILURE, PR-only → UNVERIFIED, 권한 부족·정책 거부 → BLOCKED. runner 회귀 FAIL은 VERIFIED_FAILURE가 아니다.
- T-MEM-02: 같은 event 두 번 → note 1개. 후속 revision은 live snapshot에서 최신, frozen snapshot에서는 지정 revision. RETRACTED 제외.
- T-MEM-03: 다른 repo note, cutoff 이후 note, holdout 관련 자료 → 비노출.
- T-MEM-04: exact hit·keyword hit가 인용 가능한 projection ID로 반환. no-hit → NO_HIT. 인덱스 손상 → UNAVAILABLE.
- T-MEM-05: 과거 VERIFIED_FAILURE 사례의 실패 조건이 표시되고, source가 다르면 경고가 붙는다.
- 로그 원문의 `"`, `*`, `NEAR`, `OR` 같은 FTS 연산자가 연산자로 해석되지 않는다.
- N13: 한국어·오류 토큰(`KeyError`, `inspector_id`, `미지정`) 검색 결과를 evidence에 기록한다(품질 주장은 하지 않음).

## 금지·함정

- agent가 outcome을 쓰는 endpoint를 만들지 않는다. agent가 수정한 MEMORY.md를 공유 DB로 올리지 않는다.
- 과거 패치를 자동 적용·replay하지 않는다. 사례로 검사를 건너뛰지 않는다.
- SQLite 검색을 "embedding"·"vector RAG"라고 부르지 않는다.
