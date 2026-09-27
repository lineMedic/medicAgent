# 05. 핵심 흐름과 트랜잭션 경계

> 원본: [spec 02 §3](../spec/docs/02-architecture.md), [spec 04 §7](../spec/docs/04-data-state.md), [spec 06](../spec/docs/06-broker-runner.md), [spec 08](../spec/docs/08-release-verification.md), [spec 15](../spec/docs/15-issue-intake-workflow.md), [spec 16 §3](../spec/docs/16-notifications.md), [spec 17 §2·§4·§5](../spec/docs/17-case-memory.md).
> 표기: `TX{ ... }` = 하나의 `BEGIN IMMEDIATE` 트랜잭션. `EXT:` = 트랜잭션 밖 외부 호출(GitHub·모델·SMTP·Docker). **EXT는 절대 TX 안에 두지 않는다.**

## 공통 패턴

```text
TX{ 현재 scope·version·허용 전이 검사
    UPDATE ... WHERE id=? AND version=? AND status=?     # CAS, 0행이면 STATE_CONFLICT
    논리 key unique 확인
    api_requests / executions(INTENDED) / notifications(PENDING) 기록
    audit_events INSERT }
EXT: 외부 호출
TX{ receipt·결과·상태 기록, case event 기록 }
```

외부 응답이 timeout이거나 모호하면 결과를 추정하지 않고 `UNKNOWN`으로 기록한다. 재시도는 "접수되지 않았다"는 근거가 있을 때만 한다.

---

## ① 로그 → incident (W07)

```text
MES stdout JSON log ─► detector.parse → sanitize
  signature = (service, error_type, top_frame, endpoint)   # request_id·lot_id·ts·줄번호 제외
  problem_fingerprint = sha256(version|service|error_type|top_frame|endpoint)
  같은 fingerprint가 60초 안에 3회 → 사건 후보
TX{ active incident(run, fingerprint)가 있으면 count·last_seen·evidence 추가
    없으면 incident NEW (source_kind=LOG, routing_scope, repository_id) + evidence
    audit }
→ ②로
```

- 조사 중인 work가 있는 incident에는 evidence·count만 붙인다. 새 dispatch는 없다(FR-01).
- 업무 계약을 직접 검사해 실패한 것도 `business_contract_violation` 사건을 만들 수 있다(source_kind=VERIFIER).

## ② incident → Issue 조회·생성·binding (W24)

```text
lookup(scope, repo, fingerprint):
  1. issue_bindings에 같은 scope/repo/fingerprint → EXT: Issue 재조회(실존·open/closed·repo)
  2. 우리 CREATE_ISSUE execution의 marker + bot 작성자 + receipt 일치 → binding 복구
  3. 승인된 issue form의 서비스·signature가 정확히 일치 + 허용 작성자/운영자 기록 → 재사용
  4. 제목·본문 유사도만 있음 또는 후보 여러 개 → AMBIGUOUS (운영자에게 후보 제공, 생성 안 함)
  5. 완료된 조회(모든 페이지, PR 제외)에서 후보 없음 → NO_MATCH_IN_SCOPE
  오류(권한·rate limit·네트워크·잔여 페이지) → LOOKUP_INCOMPLETE (생성·코드 작업 중단)

NO_MATCH_IN_SCOPE:
  EXT: 생성 직전 delta 재조회 (repo 단위 write 직렬화)
  TX{ executions CREATE_ISSUE INTENDED, logical_key=issue:<scope>:<repo>:<fingerprint> }
  EXT: POST issue (정제 템플릿 + marker)
  성공: TX{ execution SUCCEEDED, github_issues upsert, issue_bindings(basis=CREATED),
            work WAITING_APPROVAL 생성 }
  timeout: TX{ execution UNKNOWN, incident EXECUTION_UNKNOWN }
           → ⑦ reconcile (bot author+marker+시각 범위로 정확히 1개일 때만 채택. 0개여도 재POST 금지)
AMBIGUOUS / LOOKUP_INCOMPLETE:
  TX{ incident ESCALATED(reason), WORK_BLOCKED 알림 intent(work 없으면 notify:intake:... 키) }
```

로그 안의 Issue 번호나 HTML marker는 연결 권한이 아니다. Search API 결과 0개만으로 생성하지 않는다.

## ③ Issue polling → 새 Issue → work (W23)

```text
최초: initial_import=observe_only → 전체 mirror, 활성화 checkpoint 저장 (backlog 자동 시작 없음)
매 60초:
  since = last_checkpoint - 120s, state=all, sort=updated, direction=asc, per_page=100, max_pages=10
  EXT: 페이지 끝까지 조회 (ETag는 같은 URL·query·권한에만, 304면 기존 snapshot)
  pull_request 필드 있는 항목 제외
  max_pages 안에 못 끝내면 complete=false (no-match 확정 금지)
  TX{ github_issues upsert(payload 전체, snapshot hash), poll_event_key로 중복 제거 }
  모든 페이지 저장 후에만 TX{ integration_state checkpoint 갱신 }
새 open Issue (checkpoint 이후 생성):
  author_id ∈ trusted_author_ids, deny label 없음, assignee·기존 PR 충돌 없음
    → incident(source_kind=GITHUB_ISSUE, provisional fingerprint, count=0) + work WAITING_APPROVAL
       → 등록 정책의 자동 승인 → ④
  미승인 작성자 → WAITING_APPROVAL (읽기 전용 요약만)
  사람 작업 중 → BLOCKED(HUMAN_WORK_IN_PROGRESS)
기존 Issue의 본문 수정·bot 댓글·라벨 변경 → mirror만 갱신, 새 attempt 없음
우리가 만든 Issue → ②에서 만든 work를 같은 key로 재사용 (별도 work 생성 안 함)
closed/권한 회수 → 미시작 work 취소·차단, 실행 중이면 안전 경계에서 정지
reopened → 운영자 승인으로 새 generation (자동 재실행 없음)
```

댓글은 입력 트리거로 쓰지 않는다. 그래서 시작·완료 댓글이 루프를 만들지 않는다. 429·Retry-After를 우선하고 빠른 무한 polling을 하지 않는다.

## ④ claim → 시작 게이트 → attempt (W25·W26·W28)

```text
TX{ work WAITING_APPROVAL → WAITING_NOTIFICATION (CAS, one_active_work_per_issue)
    notifications INSERT WORK_STARTING (PENDING, logical_key) , audit }
EXT: notifier.send(github_comment, 본문+marker)              # outbox worker
  ACCEPTED(receipt_id, accepted_at) → TX{ notification ACCEPTED, work → READY }
  REJECTED(safe_to_retry) → 최대 3회 backoff (Retry-After 우선)
  UNKNOWN → TX{ notification UNKNOWN } → 재조회(reconcile), 재발송 금지
60초 안에 ACCEPTED 없음 또는 명확한 실패:
  TX{ work BLOCKED(START_NOTICE_UNCONFIRMED), incident ESCALATED, WORK_BLOCKED intent }
READY:
  EXT: Issue 최신 상태 재조회 (open? snapshot hash 동일? 권한·취소 flag?)
  TX{ 재확인 결과 유효 + one_running_work 슬롯 비어 있음
      work READY → RUNNING, incident NEW → INVESTIGATING,
      attempt 발급(ATT-), deadline=now+240s, tool_budget=15, 제출 0회 }
  memory.prepare_snapshot_context() → case_retrievals 기록 (cold_start면 DISABLED)
  agent token 발급(run·incident·work·attempt 범위)
  writable workspace 생성 (/sandbox/work/repo = base 사본)
  adapter.run_agent(run_id, incident_id, work_id, attempt_id, deadline, workspace_ref, context_ref)
```

- 시작 알림 전에도 로그 수집·읽기 전용 triage·차단 보고 작성은 허용된다. **writable workspace·attempt·패치만** 게이트 뒤다.
- UI 표시, 큐 등록, stdout 출력은 게이트를 열지 못한다. provider receipt만 연다.
- `context_ref`에는 서버가 확인한 work_id, issue_ref, issue snapshot, start receipt, run/attempt, base identity, memory mode·snapshot·retrieval_id를 넣는다.

## ⑤ 제안 → 브로커 3분기 (W09·W10·W11)

```text
POST /tools/proposals
  B01 principal·run·incident·attempt·예산  B02 schema·category/action·근거
  → TX{ proposal RECEIVED, incident INVESTIGATING→VALIDATING } → 202
백그라운드:
  B03 증거가 같은 run·사건·등록 출처    B04 기존 execution intent·PR·초안
  B05 민감 값·허용 채널                  B06 실제 액션 직전 상태·version 재확인
  create_pr:
    patch_policy(경로·크기·형태) → disposable checkout(base) → git apply --check → apply
    → 실제 tree 변경 재확인 → candidate commit (candidate_sha, candidate_tree)
    → runner R0(base 회귀) → R1(base+새 테스트: exit 1, collected≥1, failures≥1, errors=0)
    → R2(candidate: exit 0, 새 테스트·보호 회귀 모두 passed)
    실패: TX{ proposal REJECTED(check code), 수정 1회 남으면 incident VALIDATING→INVESTIGATING,
              아니면 ESCALATED/BLOCKED + WORK_BLOCKED + case BLOCKED/phase=validation }
    통과: EXT: Issue 상태·assignee·기존 PR 충돌 재조회
          TX{ executions CREATE_PR INTENDED }
          EXT: push autofix/<run>/<incident>/<proposal> → PR(base=baseline/<run>) → head SHA 재조회
          TX{ execution SUCCEEDED, incident PR_OPENED, work WAITING_REVIEW,
              PR_READY intent, case event(UNVERIFIED) }
          timeout: TX{ execution UNKNOWN, incident/work EXECUTION_UNKNOWN } → ⑦
  create_work_order_draft:
    equipment·manual_ref 확인 → 승인 템플릿으로 초안(delivery_status=not_sent, review_required=true)
    TX{ execution DRAFT_WORK_ORDER SUCCEEDED(result=초안), incident WORK_ORDER_DRAFTED,
        work HANDED_OFF, HANDOFF_DRAFTED intent, case event(HANDOFF) }
  escalate:
    TX{ incident ESCALATED, work BLOCKED(reason), WORK_BLOCKED intent(blocker report), case event(BLOCKED) }
```

PR 본문은 [spec 06 §8](../spec/docs/06-broker-runner.md) 템플릿이며 `Related to #<n>`을 쓴다. `Fixes/Closes/Resolves`를 넣지 않는다.

## ⑥ 리뷰·머지 → 릴리스 → 검증 (W12·W05)

```text
사람: PR 리뷰·squash 머지 (G7)
사람: make approve-release ... (G8) → POST /ops/releases
사전 검사 1~7 (spec 08 §2):
  operator·expected version·PR_OPENED / repo·PR·base branch가 catalog와 일치
  EXT: merged=true, head, 최종 merge SHA 조회 / reviewer가 본 head = candidate
  최종 tree == candidate tree / 현재 image == expected_current_image_id / 기존 execution 확인
TX{ executions DEPLOY INTENDED(logical_key=deploy:<work>:<sha>), incident DEPLOYING,
    work WAITING_VERIFICATION, run-level lock }
EXT: exact SHA fetch·checkout → 재현·회귀 재실행 → 신뢰 레시피로 빌드 → image ID로 기동
     → docker inspect(container·image·label) 확인
TX{ execution SUCCEEDED(image_id, container_id), incident VERIFYING, verification RUNNING }
verifier: t0 = 대상 컨테이너 기동 + 로그 수집 대상 확인 시점(monotonic)
  t=0/10/20/30s 모든 case 호출, 로그 스트림 연속 읽기, container·image 불변 확인
  t<60s에는 PASS 금지. 반증 관찰 시 FAIL 조기 종료 가능
TX{ verification PASS → incident RESOLVED, work SUCCEEDED, RECOVERY_VERIFIED intent, case VERIFIED_SUCCESS
    FAIL → ESCALATED/BLOCKED, RECOVERY_NOT_VERIFIED, case VERIFIED_FAILURE
    INCONCLUSIVE → ESCALATED/BLOCKED, RECOVERY_NOT_VERIFIED, case INCONCLUSIVE }
```

자동 rollback·자동 재조사·추가 패치는 없다. 이전 image ID를 기록해 사람이 복원한다.

## ⑦ UNKNOWN 조정 (W11·W24·W26, core=운영자 CLI)

```text
make reconcile EXECUTION_ID=...          # 또는 make notification-reconcile NOTIFICATION_ID=...
  EXT: 정확한 identity로 외부 상태 조회
    CREATE_ISSUE: bot author + marker + 생성 시각 범위 → 정확히 1개면 채택
    CREATE_PR: head/base/marker/candidate 일치하는 PR
    DEPLOY: 실제 container·image
    알림: bound Issue의 bot 댓글 marker + body hash (다른 작성자 댓글 채택 금지)
  결과 FOUND → TX{ 해당 상태로 복구 (03-domain-model §2 EXECUTION_UNKNOWN 전이) }
       CONFIRMED_ABSENT / CONFLICT / INCONCLUSIVE → TX{ 기록 } → 운영자 판단 (자동 재실행 없음)
```

한 번 "없음"으로 조회됐다고 즉시 재생성하지 않는다. 자동 bounded 재조회는 H04다.

## ⑧ 재시도·취소 (W25)

```text
retry: POST /ops/work-items/{id}/retry (terminal BLOCKED/HANDED_OFF, blocker_resolution_note)
  TX{ 새 incident(reopened_from=이전), 새 work generation+1 (WAITING_APPROVAL→...), audit }
  → ④부터 다시: 새 시작 알림, 새 attempt
cancel: POST /ops/work-items/{id}/cancel
  시작 전(WAITING_*/READY) → CANCELLED + WORK_CANCELLED 알림
  RUNNING → cancel_requested=1, 안전 경계에서 정지. 외부 결과 불명이면 UNKNOWN 처리 후 결정
```

같은 run에서 BLOCKED/HANDED_OFF work에 새 로그가 붙어도 자동 재시작하지 않는다. 닫힌 Issue를 무단 reopen하지 않는다.

## ⑨ run 생성·reset·archive (W19)

```text
make run-new → 새 run_id, demo_runs(active=1, 이전 run active=0), config hash, host manifest,
               EXT: baseline/<run_id> 브랜치 생성(setup credential), routing_scope=eval:<run_id>,
               memory mode/snapshot 선택
make reset RUN_ID=... → 새 intake·dispatch 정지 → UNKNOWN·SENDING 확인(reconcile 또는 미해결 기록)
               → make export-run → run ID 라벨이 붙은 container·workspace만 정리 → run-new
보존: 원격 baseline/autofix 브랜치, Issue, PR, case note, 이전 run DB 기록
정지(W19 D86): run active=0 → 그 run의 make start 루프가 새 일·외부 쓰기를 멈춘다. 새 run 프로세스는 자기 run 행만 처리한다
금지: DB 삭제, force push, 원격 main 되돌리기, docker prune, 과거 알림을 다른 Issue로 재전송
```

## 사례 기억 흐름 (W27·W28)

```text
case builder: 다음 event를 source_event_key로 한 번만 처리
  proposal 검사 결과 / PR_READY / HANDOFF_DRAFTED / WORK_BLOCKED / verification 최종 결과
  → 사실 필드는 DB 원본에서, outcome은 03-domain-model §6 규칙으로
  → 정제 → publish(PUBLISHED) + FTS index insert (같은 TX) / 정제 실패 시 DRAFT
  → 나중 결과는 같은 series의 새 revision(supersedes_id), 잘못된 자료는 RETRACTED
search (시작 게이트 통과 후 1회 + agent의 추가 호출):
  mode=cold_start → DISABLED
  mode=memory_assisted → snapshot manifest의 note ID/revision/hash만 대상
    ACL·repo·service·publish·snapshot 필터 먼저 → exact fingerprint + FTS5 BM25
    → outcome별 후보 병합, 관련 실패/차단 최소 1건 포함(있을 때), top_k ≤ 5, snippet ≤ 2,000자
    → 현재 incident에 history projection evidence 생성 → case_retrievals 기록
```
