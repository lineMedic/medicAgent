# W23 — Issue mirror·bounded polling·checkpoint

| 항목 | 값 |
|---|---|
| 등급 | core |
| 자율성 | C(G2) — fake로 UNIT_TESTED, live 감지 1회는 게이트 후 |
| 선행 | W22 |
| 목표 상태 | LIVE_VERIFIED |
| 원본 근거 | [spec 15 §2·§3.3·§5](../spec/docs/15-issue-intake-workflow.md), [spec 04 §1·§8](../spec/docs/04-data-state.md), [spec 14 W13·W15](../spec/docs/14-decisions-sources.md#w15) |
| 참조 | [docs/05 ③](../docs/05-workflows.md), [docs/03 §7](../docs/03-domain-model.md), [docs/07 §5](../docs/07-constants.md) |
| 요구·테스트 | FR-18, INV-13, AC-S5(일부) / T-ISS-05, T-ISS-06(일부), S5-new |

## 목표

최초 동기화는 관찰만 하고(backlog 자동 실행 없음), 이후 60초마다 겹침 구간을 두고 변경을 읽어 mirror를 갱신한다. checkpoint 이후 생긴 **승인된 작성자의 새 open Issue**만 incident·work 후보가 되고, 미승인·사람 작업 중인 Issue는 자동 수정되지 않는다.

## 만들 파일

- `linemedic/control_plane/issue_sync.py`
  - `initial_import()`: `state=all` 전체 페이지 → mirror, `integration_state`에 활성화 checkpoint와 `complete` 여부
  - `poll_once()`: `since = checkpoint − overlap(120s)`, `sort=updated`, `direction=asc`, `per_page=100`, `max_pages=10`. `pull_request` 필드 항목 제외. 모든 페이지를 저장한 뒤에만 checkpoint 갱신(서버 시각 경계와 fetched max 시각 저장). 같은 timestamp는 겹쳐 읽고 `poll_event_key`로 중복 제거
  - ETag는 같은 URL·query·권한 범위에만 사용, 304면 기존 snapshot 사용
  - 429·Retry-After 우선, 빠른 무한 polling 금지. 주기적 full reconciliation(예: 10회마다 전체 페이지)으로 delta 누락 확인
  - `snapshot_sha256(issue)`: [docs/03 §7](../docs/03-domain-model.md) 필드만. mirror `payload_json`에는 전체 payload
  - 새 Issue 처리: `author_id ∈ trusted`, deny label 없음, 사람 assignee·타인 PR 충돌 없음 → incident(`source_kind=GITHUB_ISSUE`, provisional fingerprint `issue:<repo_id>:<number>`, count 0) + work `WAITING_APPROVAL` 생성 → W25의 자동 승인 정책 호출. 그 밖은 `WAITING_APPROVAL`(읽기 전용 요약) 또는 `BLOCKED(HUMAN_WORK_IN_PROGRESS)`
  - 기존 Issue의 본문·라벨 변경·bot 댓글 → mirror만 갱신, 새 attempt 없음. snapshot hash가 바뀌고 활성 work가 있으면 W25의 scope 재검사 경로로 넘긴다
  - 우리가 만든 Issue(W24 CREATE_ISSUE)는 이미 만든 work를 같은 key로 재사용
  - closed·권한 회수 → 미시작 work 취소·차단 요청. reopened → 자동 실행 없음(운영자 승인 필요)
- `ops_api.py` 추가 — `POST /ops/integrations/github/sync`(등록 repo 1회 조회, repo 지정 불가)
- CLI·Makefile — `make issue-sync RUN_ID=`
- supervisor 루프 연결점 — `start` 시 poll 루프를 돌릴 수 있게 함수만 노출(실제 기동은 W13)
- 테스트: `integration/test_issue_polling.py`, `live/test_issue_live.py`의 S5-new 부분

## 수용 기준

- T-ISS-05: 초기 backlog 50개 → 자동 work 0개. 미승인 작성자 새 Issue → WAITING_APPROVAL, 자동 수정 0건. 다른 repo 데이터 없음.
- T-ISS-06(일부): closed Issue·사람 assignee → 자동 작업 없음, 충돌 보고.
- PR 항목은 mirror에 들어가지 않는다. 10페이지 cap에서 잔여 페이지가 있으면 `complete=false`.
- 두 번째 페이지 조회 중 실패 → checkpoint가 앞으로 가지 않는다. 재시작 후 이어서 읽어도 work가 중복되지 않는다.
- bot 시작 댓글로 `updated_at`이 바뀌어도 snapshot hash는 같고 새 work가 없다.
- live S5-new: 승인된 작성자가 만든 새 Issue 1개를 polling으로 감지(감지 시각과 Issue 생성 시각 기록). "60초 안 탐지"를 SLA로 적지 않는다.

## 금지·함정

- 댓글을 작업 트리거로 쓰지 않는다(bot loop 방지).
- `author_association`·본문의 "승인함"을 권한으로 쓰지 않는다.
- webhook endpoint를 만들지 않는다(P1).
