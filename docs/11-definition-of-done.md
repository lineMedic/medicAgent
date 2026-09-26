# 11. 완료 정의·증거·주장 규칙

> 원본: [spec 10 §7](../spec/docs/10-delivery-plan.md), [spec 01 §6](../spec/docs/01-requirements.md), [spec 12 §7](../spec/docs/12-nvidia-requirements.md), [spec 13 §2·§5·§8](../spec/docs/13-demo-submission.md), [spec templates](../spec/templates/implementation-handoff.md).

## 1. 카드 상태

| 상태 | 올릴 수 있는 조건 | 증거 |
|---|---|---|
| `NOT_CHECKED` | 초기값 | — |
| `NOT_STARTED` | 선행·게이트를 확인했고 아직 착수 전 | — |
| `IMPLEMENTED` | 카드의 파일을 만들었고 테스트가 아직 없거나 일부 실패 | 커밋 해시 |
| `UNIT_TESTED` | 카드의 테스트 ID가 `make test`(또는 `make test-docker`)에서 모두 통과 | 명령, 통과 수, 커밋 해시 |
| `LIVE_VERIFIED` | 카드가 요구하는 실제 외부 경로(GitHub·모델·sandbox·Docker 배포)로 확인 | `evidence/` 또는 `runs/<run_id>/`의 원본 경로, receipt·ID |
| `BLOCKED` | 진행 불가 | 사유, 필요한 게이트, 시도한 것 |

- 한 카드 안에서 fake 부분은 UNIT_TESTED, live 부분은 게이트 대기일 수 있다. STATUS.md 작업표의 상태에 `UNIT_TESTED (live: BLOCKED_ON_HUMAN G2)`, 게이트·차단 칸에 필요한 것과 확인 방법을 직접 적는다. `BLOCKED_ON_HUMAN`은 차단 설명이며 별도 카드 상태 enum이 아니다.
- 개발 진행 상태와 완료 보고는 STATUS.md에만 기록한다([D62](../DECISIONS.md)). 카드의 상태·증거를 갱신하고 완료 또는 대기 시 현재 작업을 비운 뒤, 진행 가능한 다음 카드나 대기 사유를 함께 적는다.
- 카드의 `목표 상태`에 도달해야 다음 카드의 선행 조건이 충족된다. 단, 선행 카드의 live 부분만 남았다면 fake 구현 위에서 다음 카드를 진행할 수 있다(카드에 명시된 경우).

### 기록 점검 (완료·차단·재개 때)

- 상태가 위 enum과 카드 목표에 맞고, 다음 작업의 선행과 해당 단계에 필요한 게이트를 확인했는가?
- 실행 명령·결과·환경(fake/mock, Docker, live)을 구분했고, 원본 증거 경로가 실제로 존재하며 그 내용이 주장을 뒷받침하는가? live 증거는 `evidence/` 또는 `runs/<run_id>/`에서 확인한다.
- 게이트 요청·완료 시각과 승인 범위·근거를 기록했는가? G7·G8은 해당 run의 사람 행동을 확인했는가?
- 현재 작업·다음 작업·작업표·완료 보고가 일치하고, 차단된 카드에는 남은 일과 재개 조건이 있는가?
- 시각은 UTC로, 체크포인트 목표만 KST로 적었는가? 토큰·키·비밀번호·수신 주소가 포함되지 않았는가?

## 2. 증거 규칙

- 증거는 **원본**이어야 한다: 명령 출력, receipt ID, 커밋 SHA, PR 번호, image ID, 로그 경로. 요약만 적지 않는다.
- 실행하지 않은 것은 `NOT_RUN`, 관측하지 못한 값은 `null`, 해당 없는 칸은 `N/A`다. 셋을 섞지 않는다.
- 측정 범위가 불완전하면 "금지 행동 0건" 같은 값을 0으로 채우지 않는다. 미확인이다.
- 사람이 패치를 고쳤거나 대신 작성했으면 `human-edited`/`human-authored`로 분류하고 agent 성과에서 뺀다.
- live 실행 기록은 [spec templates/run-record.md](../spec/templates/run-record.md) 양식으로 `runs/<run_id>/run-record.md`에 남긴다. 차단은 [blocker-report.md](../spec/templates/blocker-report.md), 사례는 [case-note.md](../spec/templates/case-note.md) 구조를 따른다.

## 3. 카드 완료 보고 양식

STATUS.md의 `완료 보고 기록`에 최신 보고를 위로 추가한다. 중단한 카드도 남은 일·재개 조건을 기록한다. 카드 밖의 문서 변경은 제품 카드 상태를 올리지 않고 별도 보고로 남긴다.

```markdown
### Wxx 완료 보고 (YYYY-MM-DDTHH:MMZ)
- 상태: UNIT_TESTED (live: NOT_RUN — G2 대기)
- 변경 파일: linemedic/control_plane/verifier.py (판정 로직), ...
- 실행: `make test` → 42 passed / `make test-docker -k verifier` → NOT_RUN (docker 없음)
- 테스트 ID: T-VERIFY-01 PASS, T-VERIFY-02 PASS, T-VERIFY-04 PASS, T-VERIFY-05 PASS
- 증거: 커밋 abc1234..., evidence/...
- 남은 일·위험: ...
- 다음 카드: W06
```

## 4. 주장 금지 목록

README, 발표, 신청서, STATUS, 커밋 메시지 어디에도 아래처럼 쓰지 않는다.

| 쓰지 않는 표현 | 대신 쓰는 표현 |
|---|---|
| 자동 복구 완료 (PR만 있음) | PR 준비, 사람 리뷰·배포 승인 필요 |
| 정비 완료 (초안만 있음) | 정비 요청 초안, 담당자 확인 필요 |
| 사람이 읽음 / 메일 배달됨 (receipt만 있음) | 댓글 등록 / 메일 서버 접수 |
| 정답 사례 (UNVERIFIED) | 아직 업무 검증 없는 시도 |
| 실패했으니 재실행 (EXECUTION_UNKNOWN) | 외부 조치 여부 미확인 |
| 모든 장애 해결 (RESOLVED) | 지정한 업무 계약·관찰 범위 통과 |
| NVIDIA embedding / vector RAG (SQLite FTS5) | SQLite FTS5 lexical 검색 |
| 메일 발송 (GitHub 댓글만) | GitHub Issue 댓글 알림 |
| 실시간 Issue 감시 (polling) | 60초 주기 polling |
| 온프레미스 추론 (cloud endpoint) | cloud endpoint 사용, 온프레미스 NIM은 계획 |
| 두 runtime 사용 (하나만 실행) | 실제 사용한 runtime 하나 |
| R1/R2 충족 (답변 없음) | UNCONFIRMED |
| 조종당해도 무엇도 못 한다, 비밀이 전혀 없다, 모든 공격 차단, PLC에 안전, 완전 폐쇄망 | 시험한 목적지·정책·실행 모드·잔여 위험 |
| 무개입 자동화 (사람 승인 포함) | 사람 검토·승인 전후 구간의 자동화 |
| 3/3 성공 → 산업적 성공률·MTTR 개선 | 작은 반복 시험 3회 결과 |
| 규칙 기반보다 우수 (동일 조건 비교 없음) | (주장하지 않음) |

## 5. 화면·알림의 상태 문구 ([spec 13 §2](../spec/docs/13-demo-submission.md))

| 저장 상태 | 표시 | 금지 표현 |
|---|---|---|
| work WAITING_APPROVAL | 처리 범위 승인 대기 | 자동 처리 시작 |
| WAITING_NOTIFICATION | 시작 알림 확인 대기, 수정 미시작 | 작업 중 |
| RUNNING | 원인 조사·수정안 작성 중 | 복구됨 |
| WAITING_REVIEW / PR_OPENED | PR 준비, 사람 리뷰·배포 승인 필요 | 자동 복구 완료 |
| HANDED_OFF / WORK_ORDER_DRAFTED | 정비 요청 초안·담당자 확인 필요 | 정비 완료 |
| BLOCKED / ESCALATED | 진행 불가, 사유와 필요한 조치 | 사유 없는 단순 오류 |
| EXECUTION_UNKNOWN | 외부 조치 여부 미확인 | 실패했으니 재실행 |
| RESOLVED / SUCCEEDED | 지정한 업무 계약·관찰 범위 통과 | 모든 장애 해결 |
| notification ACCEPTED | 댓글 등록 / 메일 서버 접수 | 사람이 읽음 |
| case UNVERIFIED | 아직 업무 검증 없는 시도 | 정답 사례 |

## 6. v4 전체 완료의 조건

S1·S2-lite·S1b·S3에 더해 S4~S7을 실제로 확인해야 "v4 완료"라고 쓴다([spec 00 §8](../spec/00-MASTER-PLAN.md)). 시간이 모자라면 구현한 경로와 미구현 항목을 분리한다. v3 core만 동작하면 "Issue 자동화와 사례 재사용은 설계 단계"라고 적는다. 제출 직전 점검 목록은 [tasks/W21](../tasks/W21-readme-submission.md)에 있다.
