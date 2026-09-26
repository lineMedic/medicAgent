# 15. GitHub Issue 연결·감시·작업 lifecycle

> **v4 신규 규범 문서.** 사용자 요구인 ‘로그 → 기존 Issue 또는 신규 Issue → 작업’과 ‘새 Issue → 작업 전 알림 → 작업’을 정의한다. GitHub UI를 화면 스크래핑하지 않고 등록된 repo의 API를 사용한다. 아래 endpoint와 정책은 구현 설계이며 현재 연결된 운영 자동화가 아니다.

## 1. 서로 다른 개념

| 개념 | 식별 | 역할 |
|---|---|---|
| GitHub Issue | `repository_id + issue_number`, node ID | 사람과 에이전트가 함께 보는 티켓 |
| Incident | `run_id + incident_id` | 어느 실행에서 관찰된 증상과 증거 |
| Work item | `routing_scope + repo + issue + generation` | 승인·시작 알림·조사·리뷰 대기의 단일 작업 |
| Attempt | `attempt_id` | 한 번의 실제 에이전트 조사, 예산·sandbox·base 고정 |
| Case note | `note_id + revision/source_event_key` | 과거에 관찰한 시도와 검증 수준 |

하나의 Issue에 여러 시기의 incident가 연결될 수 있지만 동시에 활성 work는 하나다. 같은 오류 로그가 다시 들어왔다고 열린 PR 작업을 새로 시작하지 않는다. 하나의 work가 조사 중인 동안 들어온 로그는 그 work의 incident에 evidence/count로 합친다. terminal 뒤 재시도는 명시적인 새 generation이다.

## 2. 등록 범위·권한 설정

아래는 **LineMedic 자체 설정 예시**다. 예시 숫자는 실제 GitHub ID가 아니다. 운영자는 repo ID·이름 대응을 API로 확인하고 이름 변경·이전을 재승인한다.

```yaml
issue_intake:
  schema_version: linemedic.v4
  enabled: true
  repository_id: 100001
  repository_full_name: demo-team/l3-mes-api
  service_id: mes-api
  routing_scope: live
  transport: polling
  poll_interval_seconds: 60
  overlap_seconds: 120
  initial_import: observe_only
  per_page: 100
  max_pages: 10
  auto_start:
    mode: trusted_authors
    author_ids: [200001, 200002]
    deny_labels: [linemedic-ignore, needs-human]
  closed_issue_policy: require_operator
  start_notification_route_id: github-issue-primary
```

정책은 모델이 변경하지 못한다. `trusted_authors`는 사전에 자동 처리를 동의한 작성자 ID만 허용한다. `author_association` 문자열·본문의 ‘승인함’·모델이 추측한 담당자는 권한 근거가 아니다. 등록 밖 작성자는 읽기 전용 수집 후 `WAITING_APPROVAL`이다. 다른 사람이 assignee로 일하고 있거나 기존 PR이 열려 있으면 `HUMAN_WORK_IN_PROGRESS`로 멈추며, 운영자가 실제 상황을 확인한다.

초기 연결 때 기존 backlog를 전부 실행하지 않는다. `initial_import=observe_only`로 동기화한 뒤 **최초 정상 snapshot의 checkpoint 이후 만들어진 새 Issue**만 자동 입력으로 삼는다. 기존 Issue는 로그의 확정 연결 또는 운영자 요청을 통해 처리한다. author 정책이 있는 데모에서 새 Issue 생성 자체가 입력이므로 매번 라벨을 다는 것을 기본 의무로 하지 않는다.

## 3. 로그에서 기존 Issue 찾기

### 3.1 problem fingerprint와 실행 scope

`problem_fingerprint = SHA256(version + service + normalized_error_type + normalized_top_frame + endpoint_or_metric)`로 계산한다. request ID·로트 ID·timestamp·소스 줄 번호처럼 불안정한 값은 정규화에서 제외하되, 오류를 구별하는 예외 필드명 등은 유지한다. 정규화 버전도 저장한다.

Issue 입력에 오류 signature가 없으면 `issue:<repo_id>:<issue_number>` 기반 provisional fingerprint를 쓰고, 다른 Issue를 빈 signature로 합치지 않는다. 후속 로그로 안정 signature가 확인되면 같은 Issue/work에 alias binding을 추가하고 결정 근거를 남긴다. 실행 중 work를 별도 작업으로 복제하지 않는다.

fingerprint는 중복 후보 탐색 키이지 동일 원인의 증명이 아니다. `run_id`를 fingerprint 내용에 넣지 않는다. 실행 격리는 별도 `routing_scope`로 한다. 예선의 각 cold run은 `eval:<run_id>`, 실제 상시 서비스는 `live`를 사용한다. 사례 검색은 명시적인 corpus snapshot으로 과거 run 접근을 허용한다.

### 3.2 매칭 순서

| 순서 | 결과 | 행동 |
|---|---|---|
| 1 | DB의 같은 scope/repo/fingerprint binding | GitHub에서 Issue 실존·현재 open/closed·repo를 재조회 |
| 2 | 우리 CREATE_ISSUE execution에 연결된 marker와 bot 작성자·receipt가 일치 | 복구된 binding으로 재사용 |
| 3 | 승인된 issue form의 서비스·오류 signature가 정확히 일치하고 허용된 작성자/운영자 연결 기록 있음 | 증거와 mapping 결정을 남기고 재사용 |
| 4 | 제목·본문·LLM 의미 유사도만 비슷함 또는 후보 여러 개 | `AMBIGUOUS`, 운영자에게 후보 제공. 임의로 묶거나 새 Issue 생성하지 않음 |
| 5 | 완료된 조회 scope에서 후보 없음 | `NO_MATCH_IN_SCOPE`, 생성 직전 갱신 확인 후 신규 생성 |
| 오류 | 권한 오류·rate limit·네트워크·잔여 페이지·동기화 누락 | `LOOKUP_INCOMPLETE`, 신규 생성·코드 작업 중단 |

로그 안의 Issue 번호나 HTML marker는 자동 연결 권한이 아니다. 원문 인용은 제안일 뿐, repo·작성자·실제 이력·정책으로 확인한다. 자연어 유사도를 쓰지 않는 core에서는 놓칠 수 있는 중복을 ‘완벽히 제거’했다고 주장하지 않는다.

### 3.3 조회의 완전성

등록 repo의 Issue 목록을 `state=all`로 페이지 끝까지 받아 로컬 mirror를 만든다. `pull_request` 필드가 있으면 Issue 입력에서 제외한다. max_pages 안에 완료하지 못하면 `complete=false`로 저장하고 no-match를 확정하지 않는다. GitHub Issues endpoint에 PR이 포함될 수 있음과 pagination은 [W13](14-decisions-sources.md#w13)에 근거한다.

운영 범위를 기간·라벨로 줄이면 제외한 범위를 결과에 표시한다. ‘없음’은 **확인한 scope 안에서 대응 후보가 없음**이지 전체 GitHub에 없다는 뜻이 아니다. Search API 결과 0개만으로 신규 생성하지 않는다. full snapshot도 수신 중 바뀔 수 있으므로 생성 직전 delta 또는 직접 재조회를 하고 repository 단위 write를 직렬화한다.

사람이 동시에 새 Issue를 만드는 것까지 GitHub 원자적 트랜잭션으로 막을 수는 없다. 경합이 관찰되면 중복 후보를 보고하고 운영자가 canonical Issue를 선택한다. 타인의 Issue를 자동 삭제·닫기·병합하지 않는다.

## 4. 신규 Issue 생성

신뢰된 router가 `CREATE_ISSUE` intent를 **DB에 먼저 기록**한다. 논리 키는 `issue:<routing_scope>:<repo_id>:<problem_fingerprint>`다. 본문은 정제된 템플릿, 영향·관찰 시각·증거 요약·미확정 원인만 포함한다. raw 로그, 비밀, 실행 지시, 임의 링크를 붙이지 않는다.

성공 receipt의 repo/node ID/issue number/URL을 저장하고 직접 조회로 binding을 확인한다. 생성이 timeout이면 `UNKNOWN`이다. 동일 intent의 bot author+marker+생성 시각 범위를 재조회해 하나일 때만 채택한다. 0개라도 조회 불완전·전파 지연 가능성이 남으면 다시 POST하지 않고 운영자 확인을 기다린다. 둘 이상이면 충돌이다.

**Issue 생성은 작업의 출발점이지 모델 액션 종류가 아니다.** 이후 에이전트는 여전히 create_pr / create_work_order_draft / escalate 중 하나만 제안한다.

## 5. 새 Issue 감시: core polling

첫 snapshot 이후 60초 초기 주기로 repo Issues를 조회한다. `since`는 직전 성공 checkpoint에서 overlap 120초를 뺀 값으로 잡고 `sort=updated`, `direction=asc`와 pagination을 사용한다. 페이지 전체 저장이 끝난 뒤만 checkpoint를 갱신한다. 서버 시각 경계와 실제 fetched max 시각을 저장하고, 동일 timestamp는 겹쳐 읽어 중복 제거한다. 재시작 시 mirror/checkpoint를 이어간다.

`poll_event_key`는 repo + issue node ID + updated_at + 정규화한 관련 필드 hash로 만든다. HTTP304이면 이미 가진 snapshot의 일관된 캐시만 사용한다. ETag는 **동일 URL·query·권한 범위**에 묶는다. 주기적 full reconciliation으로 delta에서 누락될 수 있는 변경을 확인한다. 재시도는 rate-limit/Retry-After를 우선하며 빠른 무한 polling을 하지 않는다. GitHub는 webhook을 우선 권고하지만, 예선의 공개 수신 endpoint 준비 부담을 피하기 위해 제한 polling을 선택했다. [W15](14-decisions-sources.md#w15)

| 관찰 | core 행동 |
|---|---|
| 승인된 작성자의 새 open Issue | 생성/기존 work 확인 → 선점 → 시작 알림 |
| 같은 Issue의 body 수정·bot 댓글·라벨 변경 | mirror 갱신. 새 attempt 자동 생성 안 함 |
| 우리 router가 생성한 Issue | 이미 만든 work와 결합. bot 이벤트가 별도 작업을 만들지 않음 |
| 미승인 새 Issue | 승인 대기. 읽기 전용 요약만, 코드 수정 없음 |
| 재시작 중 놓친 새 Issue | durable checkpoint부터 overlap 재조회, 같은 key 중복 제거 |
| closed/삭제/권한 회수 | 미시작 work 취소 또는 차단, 실행 중이면 안전 경계에서 멈춤 |
| reopened Issue | 새 generation의 운영자 승인 필요. 열기 동작 자체로 재실행 안 함 |

자기 bot 이벤트를 전부 버리면 신규 Issue 생성 직후의 작업까지 사라질 수 있다. **CREATE_ISSUE를 완료한 router가 work를 만들고, watcher는 같은 key로 이를 재사용**한다. 댓글은 입력 트리거로 쓰지 않으므로 시작·완료 댓글이 무한 루프를 만들지 않는다.

## 6. 하나의 work item 선점과 시작 게이트

`WAITING_APPROVAL → WAITING_NOTIFICATION → READY → RUNNING`은 [04](04-data-state.md)의 원자적 전이로 처리한다. 같은 issue의 활성 work unique 제약과 version CAS를 함께 쓴다. 프로세스 하나여도 log callback과 polling task가 겹칠 수 있으므로 경합 시험은 core다.

수행 전 검사: binding이 최신·유효한가, 처리 권한이 있는가, 기존 사람 작업/PR과 충돌하지 않는가, service/base가 등록 범위인가, sandbox와 verifier가 준비됐는가. 근거가 부족하면 먼저 Issue에 부족한 자료를 요청한다. 허용하지 않는 DB 변경·외부 시스템 작업은 억지로 코드 패치로 바꾸지 않는다.

승인용 `issue_snapshot_sha256`는 repo/node ID·author ID·title/body·state·assignee·권한에 쓰는 label을 정규화해 계산한다. 단순 updated_at·댓글 수·우리 상태 댓글처럼 작업 의미와 무관한 필드는 제외한다. 그래야 시작 댓글 자체가 승인을 취소하지 않는다. 단, 요구 본문·지원 scope·사람 담당자·권한 label이 바뀌면 재승인 또는 중단한다. mirror에는 전체 수신 snapshot을 별도 보존한다.

시작 알림을 저장·발송하고 receipt를 확인한 뒤에만 새 attempt ID와 writable workspace를 만든다. 실행 직전에 최신 Issue state와 scope·취소 flag를 다시 확인한다. 알림 후 취소됐다면 ‘시작 취소’ 보고만 남기고 코드를 만들지 않는다. 상세는 [16](16-notifications.md).

## 7. 기존 Issue·PR에서 작업한다는 뜻

기존 Issue 번호를 유지하고 시작/차단/PR 준비/업무 검증 결과를 그 Issue thread에 연결한다. PR 제목·본문에는 `Related to #<number>`와 work/incident identity를 넣는다. **검증 전 `Fixes/Closes/Resolves`를 넣어 머지만으로 Issue를 자동 종료시키지 않는다.** GitHub의 closing keyword 동작은 기본 브랜치 등 조건에 의존한다. [W18](14-decisions-sources.md#w18)

같은 work가 만든 같은 candidate의 PR이 있으면 재사용한다. 이미 사람이나 다른 에이전트가 만든 PR은 자동 force-push/수정하지 않는다. core에서 타인 PR을 이어 고치는 것은 지원 밖이며 `HUMAN_WORK_IN_PROGRESS` 보고 후 명시적인 인계가 필요하다.

v3의 봇 PR·사람 리뷰·`baseline/*` 보호·squash·exact SHA 배포를 그대로 유지한다. GitHub closed state는 협업 상태일 뿐이다. 수동 closed는 verifier PASS가 아니며, core는 자동 Issue close를 제공하지 않는다. verified 댓글을 보고 사람이 정리한다.

## 8. 불가·재발·재시도

연결된 Issue가 있어도 작업이 논리상 불가능하거나 권한·자료가 부족하면 `BLOCKED`와 구체적인 reason을 기록한다. Issue는 삭제하지 않고 blocker report를 붙인다. 알림 경로조차 사용할 수 없으면 durable outbox와 관리 화면에 미전송을 남긴다.

같은 run에서 BLOCKED/HANDED_OFF인 작업에 새 로그가 붙어도 자동으로 재시작하지 않는다. 운영자가 부족한 자료·권한·사람 작업 충돌을 해결한 뒤 `retry`를 승인하면 **새 incident, 새 generation, 새 start notification, 새 attempt**다. 이전 결과와 사례는 보존한다. 복구 후 재발도 닫힌 Issue를 무단 reopen하지 않는다. 신규 recurrence Issue 생성/기존 Issue 재개는 운영자 정책 결정으로 남긴다.

## 9. P1 webhook 전환 계약

polling을 대체할 때만 구현한다. `issues` 이벤트와 필요한 action만 받으며 HMAC `X-Hub-Signature-256`를 **raw body**로 검증한다. delivery ID를 durable inbox에 저장하고 빨리 2xx를 반환한 뒤 비동기로 처리한다. redelivery는 같은 delivery ID일 수 있다. Issue 본문은 서명된 payload 안에 있어도 비신뢰 데이터다. 실행 전 실제 API 재조회·승인·중복 검사·시작 게이트는 그대로 적용한다. [W16](14-decisions-sources.md#w16), [W17](14-decisions-sources.md#w17)

## 10. 완료 증거

S4 신규/기존/모호/불완전 조회, S5 권한 없는 Issue·동시 log/poll·bot loop·초기 backlog·재시작, S6 미해결 보고를 시험한다. 실제 GitHub run에서 Issue ID, 생성/조회 receipt, 시작 댓글 ID, attempt 시작 시각, PR 링크를 연결한다. `poll interval=60`이 평균 60초 내 탐지를 보장하는 SLA는 아니다.
