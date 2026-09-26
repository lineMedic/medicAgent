# 06. 불변식과 강제 위치

> 원본: [spec 01 §3](../spec/docs/01-requirements.md), [spec 07](../spec/docs/07-security.md).
> 불변식은 **프롬프트 약속이 아니라 코드로** 강제한다. 아래 표의 "강제 위치"에 가드를 두고 "시험"으로 확인한다.

## 1. 제품 불변식 INV-01~INV-16

| ID | 불변식 | 강제 위치 | 시험 |
|---|---|---|---|
| INV-01 | `RESOLVED` 쓰기는 verifier 내부 경로만 | `state.py`: RESOLVED·SUCCEEDED 전이는 `actor=verifier`만 허용하고, 그 actor는 `verifier.py` 안에서만 생성 | T-STATE-02, T-AUTH-03 |
| INV-02 | 사람 승인 없는 외부 배포 금지 | `/ops/releases` operator 인증·사전 검사 1~7, `release.py` | T-AUTH-01, T-SOURCE-01~03 |
| INV-03 | runtime 에이전트는 Control Plane·테스트 기준·배포 권한을 바꿀 수 없음 | 파일 경계(workspace), `auth.py` scope, broker `patch_policy.py`, sandbox 정책 | T-PATCH-01, S3-B |
| INV-04 | 에이전트 출력과 생성 코드는 비신뢰 | broker 재검사, runner(network none), 배포 MES 격리 | T-PATCH-02, T-REPRO-*, S3-C |
| INV-05 | 원인 분류와 조치가 대응 | `proposals.py` category↔action 검사 | T-V4-01, 제안 schema 테스트 |
| INV-06 | 결과 불명 시 재조회 전 중복 실행 금지 | `executions` 논리 키 unique, UNKNOWN 상태에서 실행 함수 진입 거부 | T-IDEM-01, T-EXEC-01, T-NOT-03 |
| INV-07 | 증거 ID·모델 confidence는 정확성 보증 아님 | `confidence` 필드 거부, UI·PR 문구 | 문구 점검 |
| INV-08 | `WORK_ORDER_DRAFTED`·`ESCALATED`는 미복구 | 복구 집계는 RESOLVED만, 해당 상태에서 복구 전이 없음 | T-STATE-03 |
| INV-09 | 평가 정답·holdout은 항상 비공개. cold_start는 이전 해답 제외. memory_assisted는 사전 고정 사례만 | `eval/` 비노출, workspace 구성, `memory/search.py` mode·snapshot 필터 | T-MEM-03, workspace 검사 |
| INV-10 | 실제 PLC·설비 제어·현장 지시 자동 발송 금지 | 액션 목록 3개, `delivery_status=not_sent`, 네트워크 정책 | S2-lite, S3-C |
| INV-11 | Issue 생성/closed/PR merge는 업무 복구 판정 아님 | issue_sync는 incident 상태를 바꾸지 않음, PR에 closing keyword 없음 | T-ISS-06, T-STATE-02 |
| INV-12 | 시작 알림 receipt 전 코드 수정·attempt 금지 | `supervisor.start_attempt()` 진입 가드 + `READY→RUNNING` 트랜잭션 조건 + proposal 접수 시 receipt 확인 | T-NOT-01 |
| INV-13 | Issue 본문·댓글의 승인 주장이나 작성자 문자열로 권한을 주지 않음 | 숫자 `author_id` allowlist, operator 승인, `author_association` 무시 | T-ISS-05 |
| INV-14 | 사례 유사도·과거 성공은 현재 승인·정답이 아님 | 사례 인용과 무관하게 broker·verifier 전 경로 실행 | T-MEM-05, T-MEM-06 |
| INV-15 | 로그/Issue/RAG에서 받은 수신자·URL로 알림 금지 | route catalog(host config)만 사용, 본문 링크·멘션 정제 | T-NOT-06 |
| INV-16 | 외부 API 불명·조회 누락은 "없는 Issue"·"발송 완료"가 아님 | `LOOKUP_INCOMPLETE`, outbox `UNKNOWN` | T-ISS-02, T-ISS-03, T-NOT-03 |

## 2. 코딩 에이전트가 만들면 안 되는 것

| 금지 | 이유 |
|---|---|
| force-resolve, 임의 state PATCH, arbitrary exec, 임의 URL 검증 API | spec 03 §5 |
| runtime 에이전트용 `create_issue`·`comment_issue`·`send_mail`·`notify`·범용 webhook 도구 | spec 06 §10, 16 §1 |
| 시나리오 주입·reset·S1b 주입의 공개 HTTP endpoint | spec 03 §5, 08 §7 (호스트 CLI만) |
| 컨테이너를 만드는 일반 API, 비신뢰 입력으로 image·mount·argv 결정 | spec 06 §5 |
| 머지 감시 polling·webhook, 자동 머지, 자동 rollback, 자동 재조사 | spec 08 §2·§3 |
| 공개 webhook listener(P1), 메시지 브로커, vector DB | spec 02 §1 |
| agent가 case outcome을 직접 쓰는 endpoint, agent가 쓴 MEMORY.md의 자동 승격 | spec 17 §2, 05 §11 |
| Issue 자동 close, 타인 PR force-push·수정, 타인 Issue 삭제·병합 | spec 15 §3.3·§7 |
| `DELIVERED_TO_HUMAN`·`READ` 알림 상태 | spec 16 §4 |

## 3. 신뢰 경계와 접근 행렬 ([spec 07 §1·§2](../spec/docs/07-security.md))

**신뢰**: 호스트 운영자, 고정 실행기 코드, 보호 계약·테스트, 서버 action catalog, 승인된 빌드 레시피.
**비신뢰**: 로그 memo, 도구 반환 자연어, 모델 판단, 생성 diff·테스트, 그 코드를 실행하는 runner와 MES, Issue 본문·댓글, 과거 사례 텍스트.

| 주체 | 허용 | 금지 |
|---|---|---|
| agent | 자기 code copy, 해당 사건 `/tools/*`, 승인된 추론 경로 | `/ops/*`, 제어 DB, GitHub, Docker, 평가 기대값, OT |
| runner | 해당 단계의 읽기 전용 코드·테스트, 제한 `/tmp` | 네트워크, control credential, Docker socket, host home |
| candidate MES | 합성 runtime fixture 읽기, 자기 로그 | Control API, GitHub, 모델 API, OT, host secret |
| Control API | principal scope의 증거·제안 처리 | 비신뢰 command/URL을 host에서 실행 |
| broker / releaser | 등록 repo PR, 고정 runner/image 조작 | 임의 repo·경로·이미지·container 명령 |
| verifier | 정해진 MES HTTP 경로, host 관찰 metadata | agent가 편집한 계약, 임의 URL, 임의 복구 |
| notifier | 선택한 고정 route의 쓰기 | 사용자 입력 주소, 임의 외부 전송 |
| operator | 승인·초기화·reconcile | 검증 없는 RESOLVED 강제 |

## 4. 보호 실패 정책

- 보호 profile이 검증되지 않았거나 필수 검사를 쓸 수 없으면 새 외부 작업을 하지 않는다(`PROTECTION_UNAVAILABLE`).
- 고정 규칙·계약·runner image hash가 바뀌면 기존 PASS를 재사용하지 않는다.
- DB 기록이 실패하면 조치 전에 중단한다. 외부 실행 후 결과 저장이 실패하면 실제 상태를 재조회해 UNKNOWN을 푼다.
- 필수 sandbox 보호를 적용하지 못한 상태는 `sandbox_verified=false`로 기록하고, local 모드 결과와 섞지 않는다.

## 5. 발표·문서에서 쓰지 않는 표현 ([spec 07 §8](../spec/docs/07-security.md))

"조종당해도 무엇도 못 한다", "sandbox 탈출해도 피해가 작다", "비밀이 전혀 없다", "모든 공격 차단", "PLC에 안전", "완전 폐쇄망 실행". 대신 **시험한 목적지·정책·실행 모드·잔여 위험**을 적는다. 전체 목록은 [11-definition-of-done.md §4](11-definition-of-done.md).
