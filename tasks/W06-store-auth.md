# W06 — DB·상태 전이·감사·인증·멱등성

| 항목 | 값 |
|---|---|
| 등급 | core (H02 409 포함 — v4에서 core로 승격) |
| 자율성 | A |
| 선행 | B00 |
| 목표 상태 | UNIT_TESTED |
| 원본 근거 | [spec 04 전체](../spec/docs/04-data-state.md), [spec 03 §1·§5·§6](../spec/docs/03-api-contracts.md), [spec 07 §2·§3](../spec/docs/07-security.md), [PACKAGE-VALIDATION §3](../spec/PACKAGE-VALIDATION.md) |
| 참조 | [docs/03](../docs/03-domain-model.md), [docs/04 §1](../docs/04-api-reference.md), [docs/06](../docs/06-invariants.md), DECISIONS D44·D45·D48·D50 |
| 요구·테스트 | FR-11, FR-26, INV-01, INV-06, INV-08 / T-AUTH-01~03, T-IDEM-01·02, T-STATE-02·03, DDL 20건 |

## 목표

fresh SQLite에 spec 04 DDL이 적용되고, 모든 상태 전이가 허용 표·주체·CAS를 거쳐서만 일어나며, `/tools`·`/ops` 권한이 분리되고, 같은 멱등 키·다른 본문은 409가 된다.

## 만들 파일

- `linemedic/control_plane/migrations/0001_init.sql` — spec 04 §5의 DDL **원문 그대로** + `schema_migrations(version, applied_at)`
- `linemedic/control_plane/store.py` — `connect(path)`(FK ON, WAL, busy_timeout, `isolation_level=None`), `migrate()`, `with store.tx() as tx:`(= `BEGIN IMMEDIATE` … `COMMIT`/`ROLLBACK`), `SQLITE_BUSY` bounded retry 후 예외, `cas_update(tx, table, id, expected_version, expected_status, new_status, **fields)` → 0행이면 `StateConflict`
- `linemedic/control_plane/state.py` — [docs/03 §2·§3](../docs/03-domain-model.md)의 incident·work 전이 표를 **데이터로** 정의. `transition_incident(tx, id, expected_version, to, actor, reason)`, `transition_work(...)`, `coupled_transition(...)`. 표에 없는 전이·권한 없는 actor는 예외. 전이마다 `audit_events` INSERT
- `linemedic/control_plane/audit.py` — `append(tx, run_id, incident_id, actor, event_type, payload)`
- `linemedic/control_plane/idempotency.py` — `begin(tx, principal_scope, method, path, run_id, key, body_sha256)` → `NEW` / `REPLAY(response)` / `CONFLICT` / `IN_FLIGHT`; `complete(tx, ..., response)`
- `linemedic/control_plane/auth.py` — token → principal. `AgentPrincipal(run_id, incident_id, work_id, attempt_id)`, `OperatorPrincipal(roles)`. token은 SHA-256 hash로 저장·비교. agent token은 attempt 종료 시 폐기 가능한 구조
- `linemedic/control_plane/errors.py` — 오류 외피, 코드 → HTTP 매핑(docs/03 §5). 오류 메시지에 token·환경변수·다른 사건 내용 없음
- `linemedic/control_plane/app.py` — app factory. 변경 요청 body 처리 순서: 크기 제한 → `loads_strict` → pydantic 검증. `/tools/*`·`/ops/*` 라우터 prefix 단위 권한 가드. 모든 POST에 `Idempotency-Key` 필수
- `linemedic/control_plane/ops_api.py` — `GET /ops/incidents/{id}`, `POST /ops/incidents/{id}/escalate`
- `linemedic/control_plane/runs.py` — `create_run()`(active 전환 포함), run manifest(config hash, host manifest 경로) — `make run-new`의 DB 부분
- `linemedic/common/sanitize.py` — 비밀 패턴(`ghp_`, `github_pat_`, `nvapi-`, `sk-`, `AKIA`, `-----BEGIN`, `Bearer ...`) 마스킹, `@mention` 무력화, 허용 repo 밖 URL 비활성화
- 테스트: `integration/test_ddl_constraints.py`, `unit/test_state_transitions.py`, `unit/test_auth.py`, `integration/test_idempotency.py`, `unit/test_api_contract.py`(외피·중복 key·extra field·크기), `unit/test_sanitize.py`

## 구현 단계

1. DDL 제약 20건 테스트를 먼저 쓰고 migration을 적용해 통과시킨다([docs/09 §4](../docs/09-test-matrix.md)). FTS5 항목은 W27에서 추가.
2. 전이 표를 데이터로 만들고, 표의 모든 (출발, 도착) 쌍과 표 밖의 모든 쌍을 파라미터화해 시험한다.
3. store 트랜잭션 헬퍼·CAS·audit을 구현한다. 트랜잭션 안에서 외부 호출을 못 하도록 `tx` 객체에 네트워크 클라이언트를 주지 않는다.
4. auth·errors·app factory·body 파서를 구현한다.
5. idempotency를 구현한다: 같은 키·같은 canonical body hash·COMPLETED → 저장된 응답 재반환; 다른 hash → 409 `IDEMPOTENCY_CONFLICT`; RECEIVED/UNKNOWN(처리 중·불명) → 409 `STATE_CONFLICT`(재실행 없음).
6. ops endpoint 2개와 `runs.create_run`, `make run-new`(DB 부분)를 구현한다.

## 수용 기준

- DDL 20건 통과, `PRAGMA foreign_key_check` 빈 결과.
- T-STATE-02: broker·operator actor로 RESOLVED 전이 → 거부. T-STATE-03: WORK_ORDER_DRAFTED에서 복구 전이 없음.
- T-AUTH-01: agent token으로 `/ops/*`(현재 있는 endpoint로 시험, W12에서 `/ops/releases`로 재확인) → 403, DB 변경 없음. T-AUTH-02: 다른 run·incident 조회 → 거부, 존재 여부 비노출. T-AUTH-03: body에 `actor`·`status` → 422.
- T-IDEM-01·02 통과. `schema_version: "linemedic.v2"` → 422.
- 오래된 `expected_*_version` → 409 `STATE_CONFLICT`.

## 금지·함정

- DDL을 "개선"하지 않는다. 바꿔야 하면 DECISIONS.md에 먼저 적고 `0003_*.sql`로 추가한다.
- force-resolve, 임의 state PATCH endpoint를 만들지 않는다.
- body의 역할 문자열로 권한을 판단하지 않는다.
