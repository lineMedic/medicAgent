# W26 — 알림 outbox·GitHub 댓글·시작 게이트·차단 보고

| 항목 | 값 |
|---|---|
| 등급 | core |
| 자율성 | C(G2·G10) — fake로 UNIT_TESTED, 실제 댓글 receipt는 게이트 후. SMTP는 G12 선택 시만 |
| 선행 | W22, W25 |
| 목표 상태 | LIVE_VERIFIED |
| 원본 근거 | [spec 16 전체](../spec/docs/16-notifications.md), [spec 15 §6](../spec/docs/15-issue-intake-workflow.md), [spec templates/blocker-report.md](../spec/templates/blocker-report.md), [spec 01 FR-20·FR-21·FR-24](../spec/docs/01-requirements.md) |
| 참조 | [docs/03 §3·§5·§8](../docs/03-domain-model.md), [docs/05 ④](../docs/05-workflows.md), [docs/07 §5](../docs/07-constants.md), DECISIONS D51 |
| 요구·테스트 | FR-20, FR-21, FR-24, INV-12, INV-15, INV-16, AC-S5·AC-S6 / T-NOT-01~06, S6-blocked, N12(선택 route의 실제 접수·timeout·미전송) |

## 목표

시작 알림이 bound Issue 댓글로 **실제 접수(receipt 저장)** 된 뒤에만 work가 READY가 되고, 60초 안에 확인되지 않으면 코드 작업 없이 차단된다. 모든 결과·차단 이벤트가 durable outbox를 거쳐 한 번씩 발송되고, timeout은 UNKNOWN으로 남아 재발송 대신 재조회된다.

## 만들 파일

- `linemedic/control_plane/notifications/outbox.py` — `enqueue(tx, work, event_type, payload, route_id, event_revision)`: 서버가 logical key 생성([docs/03 §8](../docs/03-domain-model.md)), 같은 키·다른 payload hash → conflict. worker: `PENDING → SENDING`(커밋) → EXT send → 결과 기록. 재시도는 "접수되지 않음"이 명확할 때만, 최대 3회 점증 backoff, Retry-After 우선, route별 직렬화. 재시작 시 SENDING → UNKNOWN
- `linemedic/control_plane/notifications/github_comment.py` — `send(notification_id, route, rendered)` → `ACCEPTED(receipt_id=comment_id, canonical_ref, accepted_at)` / `REJECTED(reason, retry_after, safe_to_retry)` / `UNKNOWN(observation)`. `reconcile(notification_id, known_identity)` → bound Issue의 댓글에서 bot author + marker `<!-- linemedic:notify id=NOT-... h=<payload_sha256 앞 16자> -->` + body hash가 모두 맞는 것만 `FOUND`; 페이지 조회 불완전이면 `INCONCLUSIVE`
- `linemedic/control_plane/notifications/templates.py` — 이벤트 7종 본문(한국어, [spec 16 §7](../spec/docs/16-notifications.md) 문체). 각 본문에 "이 메시지가 뜻하지 않는 것"을 명시(예: PR_READY는 "업무 복구 미확인"). blocker report 렌더러(필드 10개, [docs/03 §5](../docs/03-domain-model.md)) — **모델 없이** host 기록만으로 완성. 링크는 등록 repo의 Issue·PR URL만 서버가 구성, 모델 출력의 URL·`@mention`·수신 주소는 무력화
- `linemedic/control_plane/supervisor.py` 추가 — 시작 게이트: `WAITING_NOTIFICATION`에서 필수 route ACCEPTED → READY. `start_wait_seconds`(60) 초과 또는 명확한 실패 → BLOCKED(`START_NOTICE_UNCONFIRMED`) + incident ESCALATED + `WORK_BLOCKED` intent. `start_attempt()` 가드에 "start_notification_id가 같은 generation·필수 route·ACCEPTED" 조건을 채운다
- `linemedic/control_plane/notifications/smtp.py` — **G12에서 SMTP를 고른 경우에만** 같은 send/reconcile 계약으로
- `ops_api.py` 추가 — `GET /ops/notifications`(수신 주소 비노출), `POST /ops/notifications/{id}/reconcile`
- CLI·Makefile — `make notification-reconcile NOTIFICATION_ID=`
- 테스트: `integration/test_start_gate.py`, `integration/test_notifications.py`, `live/test_notification_live.py`

## 구현 단계

1. T-NOT-01을 먼저 쓴다: receipt 전에는 `start_attempt()`가 거부되고 workspace 디렉터리가 생기지 않는다.
2. outbox·adapter·템플릿을 FakeGitHub로 구현한다.
3. 시작 게이트와 60초 타임아웃을 FakeClock으로 시험한다.
4. broker(W09)의 escalate·draft, verifier(W05 2부)가 이미 넣는 outbox intent가 이 worker로 발송되는지 연결한다.
5. G2·G10 이후 live: 전용 repo의 bound Issue에 시작 댓글 1회(receipt 시각 ≤ attempt 시작 시각), S6 차단 알림 1회. `evidence/`에 comment ID와 시각 기록. 이것이 N12 스파이크 결과다(`evidence/N12-notification-route.md`: 접수 receipt, 강제 timeout 시 UNKNOWN 처리, 미전송 표시). N12가 확인되지 않으면 알림 완료를 주장하지 않고 시작 게이트는 그대로 유지한다.

## 수용 기준

- T-NOT-01: receipt 전 writable workspace·attempt·패치 0. receipt 시각 ≤ workspace 제공 ≤ agent 실행 순서가 기록된다.
- T-NOT-02: ACCEPTED 화면·기록 문구가 "댓글 등록"이며 "읽음/배달"이 없다.
- T-NOT-03: 댓글 생성 후 timeout → UNKNOWN, 재발송 0, reconcile로 FOUND. 재시작 시 SENDING → UNKNOWN. 같은 event 중복 enqueue → 1건.
- T-NOT-04: 모델 API 실패(`MODEL_UNAVAILABLE`)에서도 blocker report가 완성되고 외부 알림 또는 미전송 상태가 기록된다.
- T-NOT-05: verifier PASS 후 알림 FAILED → incident RESOLVED 유지.
- T-NOT-06: payload에 임의 수신자·URL·`@team` → catalog 밖 전송 0, 링크·멘션 무력화, 비밀 마스킹.
- 60초 초과 → BLOCKED, 이후 도착한 receipt가 work를 되살리지 않음.

## 금지·함정

- 에이전트에게 notify·comment·send_mail 도구를 주지 않는다. 알림은 host가 상태 전이에 맞춰 자동으로 보낸다.
- `DELIVERED_TO_HUMAN`·`READ` 상태를 만들지 않는다.
- 알림 성공을 복구 성공으로, 알림 실패를 복구 실패로 바꾸지 않는다.
- SMTP와 GitHub 댓글을 동시에 필수 route로 만들지 않는다(core는 required route 1개).
