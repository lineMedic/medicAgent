# W24 — 로그 → 기존 Issue 연결 / 신규 Issue 생성

| 항목 | 값 |
|---|---|
| 등급 | core |
| 자율성 | C(G2·G10) — fake로 UNIT_TESTED, S4 live는 게이트 후 |
| 선행 | W07, W23 |
| 목표 상태 | LIVE_VERIFIED |
| 원본 근거 | [spec 15 §3·§4](../spec/docs/15-issue-intake-workflow.md), [spec 04 §4·§7](../spec/docs/04-data-state.md), [spec 01 AC-S4](../spec/docs/01-requirements.md) |
| 참조 | [docs/05 ②·⑦](../docs/05-workflows.md), [docs/03 §4·§8](../docs/03-domain-model.md) |
| 요구·테스트 | FR-17, INV-11, INV-16, AC-S4 / T-ISS-01·02·03, S4-new·existing·ambiguous |

## 목표

로그 incident가 생기면 등록 repo에서 대응 Issue를 찾아 확정 연결하고, 조회가 완전한데 후보가 없을 때만 새 Issue를 만든다. 후보가 애매하거나 조회가 불완전하면 만들지 않고 멈춘다. 생성 응답이 불명이면 재생성하지 않고 재조회한다.

## 만들 파일

- `linemedic/control_plane/issue_router.py`
  - `lookup(scope, repo, fingerprint)` → 매칭 순서 1~5·오류([docs/05 ②](../docs/05-workflows.md)). 각 결과에 조회 범위·완전성·후보·근거를 `decision_json`으로
  - `create_issue_for(incident)`: 생성 직전 delta 재조회, repo 단위 write 직렬화(프로세스 lock), `CREATE_ISSUE` execution INTENDED(logical_key `issue:<scope>:<repo_id>:<fingerprint>`) → 커밋 → EXT 생성 → 결과 기록
  - Issue 본문 템플릿: 서비스·영향·관찰 시각·정제된 증거 요약·"원인 미확정"·marker `<!-- linemedic:issue scope=... fp=... exec=EXE-... -->`. raw 로그·비밀·실행 지시·임의 링크 없음. 제목에도 비신뢰 문자열을 그대로 넣지 않는다(정제·길이 제한)
  - 성공 → github_issues upsert, `issue_bindings(basis=CREATED)`, work `WAITING_APPROVAL` 생성(W25 함수 사용)
  - timeout → execution UNKNOWN, incident `EXECUTION_UNKNOWN`
  - reconcile: bot author + marker + 생성 시각 범위로 정확히 1개일 때만 채택 → incident `NEW` 복구 + binding. 0개라도 조회 불완전·전파 지연 가능성이 있으면 재POST 없이 운영자 대기. 2개 이상 → 충돌
  - AMBIGUOUS·LOOKUP_INCOMPLETE → incident `ESCALATED(reason)`, `WORK_BLOCKED` intent(work 없으면 `notify:intake:...` 키)
  - provisional fingerprint를 가진 Issue 기반 work에 나중에 안정 signature가 확인되면 alias binding 추가(작업 복제 없음)
- `ops_api.py` 추가 — `GET /ops/issues/candidates/{incident_id}`, `POST /ops/incidents/{id}/issue-binding`(등록 repo의 번호만, `expected_incident_version`, `decision_note`, basis=OPERATOR)
- CLI·Makefile — `make issue-bind INCIDENT_ID= ISSUE_NUMBER=`
- detector 연결 — 새 incident 생성 후 router 호출(같은 프로세스, 트랜잭션 밖)
- 테스트: `integration/test_issue_matching.py`, `live/test_issue_live.py`의 S4 부분

## 수용 기준

- T-ISS-01: 기존 binding → 같은 번호 재사용, 새 Issue 0개. 완전 조회·후보 없음 → Issue 1개·binding·work 1개.
- T-ISS-02: 제목만 비슷한 후보 2개 → AMBIGUOUS, 생성·패치 0. 페이지 조회 실패 → LOOKUP_INCOMPLETE, 생성 0. PR 항목은 후보가 아님.
- T-ISS-03: 생성 후 timeout → UNKNOWN, 두 번째 POST 0회, reconcile로 1개 채택. 다른 작성자가 같은 marker를 쓴 Issue → 채택하지 않음.
- 로그 본문 안의 `#42`·HTML marker로 연결되지 않는다.
- shadow 모드(`write_enabled=false`)에서 "만들 Issue 계획"만 출력한다.
- live: S4-new 1회(생성 receipt), S4-existing 1회(번호 유지), S4-ambiguous 1회(생성 없음) 기록.

## 금지·함정

- 자연어 유사도로 자동 연결하지 않는다. 완벽한 중복 제거를 주장하지 않는다.
- 타인의 Issue를 닫기·삭제·병합하지 않는다.
- 사람이 동시에 만든 중복 Issue는 보고만 하고 운영자가 canonical을 고른다.
