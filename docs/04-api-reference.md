# 04. API 참조 (`linemedic.v4`)

> 원본: [spec 03 API 계약](../spec/docs/03-api-contracts.md), [spec 17 §5](../spec/docs/17-case-memory.md), [spec 08 §2](../spec/docs/08-release-verification.md). 예시 JSON 전문은 spec 03에 있다.
> 이 API는 LineMedic이 새로 구현하는 계약이다. NVIDIA·OpenClaw 기본 API가 아니다.

## 1. 공통 규칙

| 항목 | 규칙 |
|---|---|
| 인증 | `Authorization: Bearer <token>`. `/tools/*`는 run·incident·work·attempt 범위의 agent token, `/ops/*`는 operator token. 서로 바꿔 쓰면 403 |
| 권한 판단 | 서버가 token에서 얻은 principal만 쓴다. body의 `actor`, `role`, `status`, `model`, `policy_version`, `X-Operator` 같은 값은 무시가 아니라 **거부**(extra=forbid) |
| 형식 | JSON UTF-8, `schema_version: "linemedic.v4"` 필수. v2 등 다른 값은 422 |
| 입력 검증 | 알 수 없는 필드, 중복 JSON key, 잘못된 enum, 객체 자리의 문자열, 크기 초과를 거부 |
| 멱등성 | 모든 변경 요청(POST)은 `Idempotency-Key` 헤더 필수. 범위 `(principal, method, path, run_id)`. 같은 키·같은 canonical body → 저장된 응답 재반환, 다른 body → 409 `IDEMPOTENCY_CONFLICT` |
| 시각·SHA | UTC RFC3339. Git SHA는 40자 전체 |
| 금지 기능 | 명세에 없는 URL·path·command 전달, force-resolve, 임의 state PATCH, arbitrary exec, 임의 URL 검증 |
| 노출 | 데모 전용 격리 네트워크. 공용망을 지나면 TLS. 공개 무인 API로 배포하지 않음 |
| 오류 누설 | 인증 오류에서 다른 사건의 존재·내용을 드러내지 않음. token·환경변수 전체를 응답·로그에 넣지 않음 |

### 응답·오류 외피

```json
{"schema_version": "linemedic.v4", "request_id": "REQ-...", "data": {}, "evidence_ids": []}
```

```json
{"schema_version": "linemedic.v4", "request_id": "REQ-...",
 "error": {"code": "STATE_CONFLICT", "message": "...", "retryable": false, "details": {"current_status": "PR_OPENED"}}}
```

오류 코드와 HTTP 상태는 [03-domain-model.md §5](03-domain-model.md)에 있다.

## 2. 에이전트 도구 `/tools/*` (정확히 9개)

| 도구 | 메서드·경로 | 요청 | 반환·제한 | 카드 |
|---|---|---|---|---|
| `get_incident` | GET `/tools/incidents/{incident_id}` | — | 사건·특징·배포 identity·work·Issue ref·시작 알림·memory mode·증거 ID | W07 (W28에서 필드 추가) |
| `search_logs` | GET `/tools/incidents/{incident_id}/logs` | `q` 선택, `limit` 1~20 | 사건 ±30분 정제본, 64 KiB 상한. 정규식·shell 없음 | W07 |
| `get_deploys` | GET `/tools/incidents/{incident_id}/deploys` | — | 등록 서비스의 최근 24시간 배포, 현재 base SHA | W07 |
| `get_knowledge` | GET `/tools/incidents/{incident_id}/knowledge` | `q` 선택 | 허용된 정적 매뉴얼·런북 절. 과거 사례 아님. URL fetch 없음 | W08 |
| `query_equipment_metrics` | GET `/tools/incidents/{incident_id}/equipment/{equipment_id}/metrics` | — | 등록 설비, 최대 30분·60 sample, baseline·품질 | W08 |
| `get_bound_issue` | GET `/tools/incidents/{incident_id}/issue` | — | 서버가 확정한 repo·Issue·work·snapshot·상태. 임의 repo 검색 아님 | W28 |
| `search_cases` | GET `/tools/incidents/{incident_id}/cases/search` | `q` 선택, `limit` 1~5 | 현재 scope와 고정 snapshot 안의 사례(§5) | W27·W28 |
| `submit_proposal` | POST `/tools/proposals` | §3 union | **202 접수**(실행 성공 아님), proposal ID | W09 |
| `get_proposal` | GET `/tools/proposals/{proposal_id}` | — | 해당 사건 제안의 decision·거절 사유·정제 결과 | W09 |

- `read_file`/`grep_code`/`run_tests`는 에이전트의 **sandbox 로컬 작업**이다. 범용 파일 접근·원격 exec endpoint를 만들지 않는다.
- 응답에 `scenario_name`, 정답 category, 기대 fixture를 넣지 않는다. `features`는 힌트다.
- 과거 run의 evidence ID를 현재 제안에 직접 넣으면 `EVIDENCE_SCOPE_MISMATCH`. 과거 사례는 현재 incident에 만든 **history projection evidence ID**로만 인용한다.

## 3. 제안 스키마 (`POST /tools/proposals`)

공통 필드: `schema_version`, `run_id`, `incident_id`, `work_id`, `attempt_id`(서버 배정값과 principal 범위 일치), `category`(6종), `summary`(1~2,000자 plain text), `evidence_ids`(중복 없음, 최대 20), `action`(**정확히 한 객체**). body 최대 128 KiB.

사용하지 않는 필드(보내면 422): `actions[]`, `confidence`, `actor`, `role`, `status`, `model`, `policy_version`.

| action.type | 필드 | 규칙 |
|---|---|---|
| `create_pr` | `base_sha`, `root_cause_hypothesis`, `diff`(UTF-8 unified diff), `new_test_path`(`tests/repro/test_*.py`), `local_test_observation`(선택, 참고용) | pytest 명령·환경·GitHub URL·branch 이름은 받지 않음. 브로커가 재검사 |
| `create_work_order_draft` | `equipment_id`(catalog 등록), `symptom`, `probable_cause`(가설), `manual_ref_id`(허용 매뉴얼 조회 결과), `open_questions[]` | 자유 생성 절차·제어값·shell·URL·수신자 주소 거부. 안내 문구는 브로커가 승인 템플릿에서 채움 |
| `escalate` | `reason`(blocker_code), `open_questions[]`, `missing_requirements[]`(선택), `retry_condition`(선택) | 증거 0개 허용 |

category ↔ action 대응과 근거 개수 규칙은 [03-domain-model.md §4](03-domain-model.md).

### 접수·검증 순서

1. 인증·run/work/attempt·시작 알림 receipt·Issue 현재 scope·크기·JSON 구조 검사. 실패 시 401/403/409/413/422, 외부 실행 없음.
2. 유효하면 proposal 저장 후 **202**. 검증은 백그라운드 작업.
3. `RECEIVED → CHECKING → ALLOWED / REJECTED`. ALLOWED도 외부 실행 완료가 아님.
4. 정책·테스트 거절은 원본 결과와 이유를 보존. 같은 attempt에 **수정 1회**(새 proposal ID·새 Idempotency-Key, deadline 유지).
5. 형식·정책 오류로 인한 서로 다른 제출은 합산 최대 2회. 같은 요청의 네트워크 재전송은 예산을 쓰지 않음. 두 번째도 실패하면 이관.
6. 외부 실행 intent가 이미 있는 제안은 수정·재실행하지 않음. 불명확하면 reconcile 전까지 중단.

## 4. 운영 API `/ops/*`

| 메서드·경로 | 주체 | 의미 | 카드 |
|---|---|---|---|
| GET `/ops/dashboard` | operator read | 상태·관찰·검사 읽기 모델 | W18 |
| GET `/ops/incidents/{id}` | operator read | 감사·proposal·execution·verification 연결 | W06 |
| POST `/ops/integrations/github/sync` | operator integration | 등록 repo 조회 1회. repo/URL 지정 불가 | W23 |
| GET `/ops/issues/candidates/{incident_id}` | operator read | binding 후보·match 근거·조회 완전성 | W24 |
| POST `/ops/incidents/{id}/issue-binding` | operator triage | 등록 repo의 Issue 번호를 명시 연결 | W24 |
| POST `/ops/work-items/{id}/approve` | operator authorize | 정확한 Issue snapshot·scope 승인 → 시작 게이트 | W25 |
| POST `/ops/work-items/{id}/cancel` | operator | 취소 요청. 외부 결과 불명이면 바로 CANCELLED로 만들지 않음 | W25 |
| POST `/ops/work-items/{id}/retry` | operator authorize | blocker 해소 확인 → 새 incident·generation, 새 시작 알림 | W25 |
| GET `/ops/notifications` | operator read | outbox·receipt·실패. 수신 주소 비노출 | W26 |
| POST `/ops/notifications/{id}/reconcile` | operator reconcile | provider 기록 읽기·상태 조정. 재발송 금지 | W26 |
| POST `/ops/cases/rebuild-index` | operator maintenance | PUBLISHED 노트로 index 재구축. outcome 불변 | W27 |
| GET `/ops/cases/{note_id}` | operator read | revision·source·검증 수준 | W27 |
| POST `/ops/releases` | operator approve | 정확한 최종 SHA 배포 승인 | W12 |
| POST `/ops/executions/{id}/reconcile` | operator reconcile | 외부 상태 읽기·기록만. 새 변경 금지 | W11 |
| POST `/ops/incidents/{id}/escalate` | operator | 중단 이유 기록. RESOLVED 전이 없음 | W06 |
| POST `/ops/runs` | operator demo | 새 run 준비 | W19 |
| POST `/ops/runs/{id}/archive` | operator demo | 신규 작업 중단·증거 export. 삭제 아님 | W19 |

### 최소 요청 body

| 요청 | 필드 |
|---|---|
| issue-binding | `schema_version, run_id, issue_number, expected_incident_version, decision_note` (repo는 incident의 등록 scope로 결정) |
| approve | `schema_version, expected_work_version, expected_issue_snapshot_sha256, approval_note` |
| retry | `schema_version, expected_work_version, blocker_resolution_note` (새 generation·attempt ID는 서버 발급) |
| releases | `schema_version, run_id, incident_id, work_id, proposal_id, pr_number, approved_merge_sha, expected_incident_version, expected_current_image_id, approval_note` |

URL·원격 저장소·이미지 태그는 서버 catalog에서 정한다. principal과 승인 시각은 서버가 붙인다. Issue body가 바뀌었거나 승인 조건이 철회되면 `ISSUE_SCOPE_CHANGED`.

## 5. `search_cases` 응답

```json
{
  "schema_version": "linemedic.v4",
  "request_id": "REQ-...",
  "data": {
    "retrieval_id": "RET-...",
    "mode": "memory_assisted",
    "snapshot_id": "MEM-...",
    "engine": "sqlite_fts5",
    "status": "OK",
    "hits": [{
      "note_id": "CASE-...-R1",
      "outcome": "VERIFIED_FAILURE",
      "phase": "verification",
      "summary": "...",
      "applicability_warning": "현재 코드·입력 조건에 맞는지 다시 확인해야 함",
      "evidence_id": "EV-...",
      "source_ref": {"run_id": "r-...", "verification_id": "VER-..."}
    }]
  },
  "evidence_ids": ["EV-..."]
}
```

- `evidence_id`는 현재 incident에 새로 만든 history projection이다(원본 note ID·hash·source event를 projection에 남김).
- cold_start이면 `status: "DISABLED"`, `hits: []`. 검색 실패는 `UNAVAILABLE`로, 결과 없음(`NO_HIT`)과 구분한다.

## 6. 계약 테스트 (core)

서버·client schema의 필드·enum 일치, 잘못된 category/action, `actions[]` 제출, token 교환, 다른 run 접근, 오래된 version, 위조 model/role 필드, 임의 path·URL, 202를 성공으로 오해하는 client, 같은 키 다른 body 409, 다른 work/Issue 범위, 시작 알림 미확인 상태의 제안, spoofed issue marker, history projection ACL, 중복 approval·retry. 테스트 ID 배치는 [09-test-matrix.md](09-test-matrix.md).

안전한 API를 이유로 모델의 **조사 순서까지 고정하지 않는다.**
