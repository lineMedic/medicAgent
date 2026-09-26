# 08. 고정 버전 릴리스와 독립 업무 검증

> v4: identity chain과 관찰 조건에 core/hardening을 표시했다. 코드 경로의 완료 판정 기준. 핵심은 **검사한 대상 → 사람이 승인한 대상 → 실제 실행 대상**을 연결하는 것이다. 새 evidence 플랫폼을 만들지 않고 현재 execution과 verification 기록에 identity를 붙인다.

## 1. identity chain

| 필드 | 발급·관찰 주체 | 의미 |
|---|---|---|
| `base_sha` | trusted setup + 배포 관찰 | 조사 시점의 고정 기준 코드 |
| `patch_sha256` | broker | 수신 diff의 hash |
| `candidate_sha`, `candidate_tree` | broker | 실제 적용·검사한 commit/tree |
| `pr_head_sha` | GitHub 조회 | 원격 PR의 실제 head |
| `approved_merge_sha` | 사람 승인 + GitHub merged 조회 | 실제 머지된 최종 코드 |
| `approved_tree` | release executor | 최종 커밋의 tree |
| `build_recipe_sha256` | trusted config | 실행한 고정 빌드 레시피 |
| `image_id` | Docker inspect | 실제 빌드·배포 이미지 content ID |
| `container_id` | Docker inspect | 업무 검증의 실행 대상 |
| `contract_sha256`, `fixture_sha256` | verifier | 적용한 계약·데이터 버전 |

**등급:** `build_recipe_sha256`만 `[hardening H05]`이고 나머지 필드는 모두 `[core]`다. H05 미구현 시 빌드 레시피 파일 경로와 프로젝트 저장소 커밋을 기록한다.

SHA와 tree가 모두 같은 값이어야 하는 것은 아니다. P0의 제한된 흐름에서는 **PR head = 검사한 candidate**, **최종 merge tree = candidate tree**를 요구한다. 커밋 history가 달라도 내용이 같은지 확인한다. 내용이 다르면 새 검사와 승인 없이 진행하지 않는다.

GitHub 공식 문서에 따르면 `merge_commit_sha`는 머지 전에는 test merge commit일 수 있고, 머지 후에는 merge 방식에 따라 의미가 달라진다. 반드시 `merged=true`를 확인한 최종 결과를 사용한다. [공식 출처 W03](14-decisions-sources.md#w03)

## 2. 명시적 승인 배포

core에서는 **머지 감시용** polling·webhook을 만들지 않는다. 신규 Issue polling과는 별개다. 운영자가 [03](03-api-contracts.md)의 `/ops/releases`에 PR·최종 SHA·예상 현재 image를 명시한다. command UI는 [11](11-runbook.md)의 구현 목표를 따른다.

### 사전 검사

1. operator principal과 expected incident version 확인. 상태는 `PR_OPENED`여야 한다.
2. repository·PR number·base branch가 이 run의 server catalog와 일치한다.
3. GitHub에서 merged 여부·실제 head·최종 merge SHA를 조회한다. agent/브로커에 merge 권한을 위임하지 않는다.
4. reviewer가 확인한 head와 broker candidate 연결을 확인한다. 최신 변경 승인 설정의 실제 적용 여부도 run manifest에 남긴다.
5. P0에서는 최종 tree가 검사한 candidate tree와 같아야 한다. 무관한 변경·사람이 추가한 수정이 있으면 기존 PASS를 무효화한다.
6. 현재 runtime image가 승인 요청의 `expected_current_image_id`와 일치한다. 다르면 `SOURCE_CHANGED`로 중단한다.
7. 동일 논리 작업의 execution이 있으면 기존 상태를 반환한다. 새 external intent를 중복 생성하지 않는다.

### 실행

- 승인한 merge SHA를 정확히 fetch·checkout한다. `main` 최신 상태로 바꾸지 않는다.
- 최종 코드에서 신규 재현 테스트·보호 회귀 검사를 다시 수행한다.
- 고정 build recipe와 의존성으로 이미지를 만든다. 검사하지 않은 repo 파일·hook을 빌드에서 실행하지 않는다.
- image ID를 기록하고 해당 ID로 MES를 시작한다. mutable tag만으로 실행 대상을 선택하지 않는다.
- Docker inspect 등 host 관찰로 container/image/label을 확인한다. 앱의 `/version` 자기보고만으로 identity를 확인하지 않는다.
- 실제 요청 경로와 로그 cursor를 verifier에 넘긴다. 이후 검증 중 다른 배포·주입·reset을 막는 run-level lock을 유지한다.

로컬 이미지의 `RepoDigest`가 없으면 **local image ID**를 기록한다. registry digest를 만든 것처럼 기재하지 않는다. Docker 문서는 image 참조와 digest, container ID를 구분한다. [공식 출처 W08](14-decisions-sources.md#w08)

## 3. 실패 처리와 복원 범위

| 실패 시점 | 처리 |
|---|---|
| merge/identity/승인 검사 실패 | 기존 환경 변경 없음, 이관 |
| 최종 검사·빌드 실패 | 새 배포 없음, 이전 실행을 유지한 사실을 확인하고 이관 |
| 기존 서비스 중단 후 후보 기동 실패 | 자동으로 이전 서비스가 유지된다고 하지 않음. 실제 상태 기록·이관 |
| 배포 API timeout | `EXECUTION_UNKNOWN`, 정확한 container/image 재조회 |
| 다른 운영자 변경 감지 | 덮어쓰기 금지, 충돌로 이관 |
| 업무 검증 FAIL/INCONCLUSIVE | `ESCALATED`, 자동 재조사·추가 패치·자동 rollback 없음 |

이전 image ID·runtime 설정을 기록해 **사람이 실행하는 복원 절차**를 제공한다. 자동 rollback은 P0가 아니다. 복원 성공도 현재 업무 검사를 통과하지 않으면 `RESOLVED`가 아니다. 무중단·원자적 배포를 보장하지 않는다.

## 4. 업무 계약: defect-summary-v1

원안의 업무 규칙을 유지한다. 불량 record 하나는 불량 건수 하나다. `inspector_id`가 없는 record도 전체 집계에서 유지하고 `미지정`에 포함한다. 빈 배열은 0과 빈 집계가 정상이다. `null`·빈 문자열 등 추가 입력 정책은 별도 합의 없이 확장하지 않는다.

**원래 사용자 요청 경로** `GET /defects/summary?lot_id=...`로 검사한다. 내부 함수만 직접 호출하거나 다른 서비스의 정상 endpoint로 대신하지 않는다.

```yaml
# LineMedic이 구현할 사용자 정의 계약. NVIDIA 설정 스키마가 아니다.
contract_id: defect-summary-v1
entry_service: mes-api
cases:
  - id: missing-inspector
    request:
      method: GET
      path: /defects/summary
      query: {lot_id: L3-0927-118}
    expect:
      status: 200
      body:
        lot_id: L3-0927-118
        total_defects: 7
        by_inspector: {I-01: 3, I-02: 2, 미지정: 2}
  - id: normal-regression
    request:
      method: GET
      path: /defects/summary
      query: {lot_id: L3-0927-101}
    expect:
      status: 200
      body:
        lot_id: L3-0927-101
        total_defects: 3
        by_inspector: {I-01: 2, I-02: 1}
  - id: variant-held-out
    fixture_ref: holdout-defects-v1
assertions:
  - strict_response_schema
  - exact_lot_id
  - exact_total_defects
  - exact_by_inspector_mapping
  - sum_groups_equals_total
observation:
  samples: 4
  interval_seconds: 10
  recurrence_window_seconds: 60
  require_all_samples: true
  require_log_observer_healthy: true
  require_runtime_identity_unchanged: true
```

예시 fixture는 v2 설계값이며 실측 결과가 아니다. 위 검사자별 값과 holdout 입력은 [09](09-scenarios-evaluation.md)에서 정의한다. 모델용 문서에는 업무 규칙을 제공할 수 있지만 평가 계약 전체·holdout 기대값을 자동 주입하지 않는다.

JSON number는 실제 정수인지 확인하고 bool·음수·문자열 숫자를 통과시키지 않는다. key 누락, 다른 lot ID, 잘못된 집계, 다른 schema의 HTTP 200을 실패시킨다.

## 5. 검증 시간과 판정

`t0`는 정확한 candidate 컨테이너가 실행되고 로그 수집 대상이 확인된 뒤의 monotonic 시각이다. t=0·10·20·30초에 모든 contract case를 호출하고 **t=60초가 지나기 전에는 PASS하지 않는다**. 지속적인 synthetic 업무 요청 또는 수집 heartbeat로 관찰 구간의 수집 생존성을 확인한다.

| verdict | 조건 | incident |
|---|---|---|
| `PASS` | 모든 sample의 모든 case 통과, 60초 내 동일 오류 재발 없음, observer 정상, identity·fixture 불변 | `RESOLVED` |
| `FAIL` | 응답 mismatch·업무 오류·동일 오류 재발 등 실제 반증 관찰 | `ESCALATED` |
| `INCONCLUSIVE` | 수집 중단·timeout 원인 불명·runtime identity 변경·증거 누락 | `ESCALATED` |

**관찰 조건의 등급**

| 등급 | "observer 정상"의 정의 |
|---|---|
| `[core]` | 검증 대상 container의 로그 스트림을 t0부터 t=60초까지 끊김 없이 읽었고(`docker logs --follow` 등 호스트 관찰), 그동안 container ID·image ID가 바뀌지 않았으며, 표본 요청이 모두 응답했다 |
| `[hardening H03]` | 위에 더해 주기적 synthetic 요청의 로그 heartbeat와 cursor 연속성으로 관찰 공백을 탐지한다 |

요청이 timeout됐으면 성공이 아니며, timeout의 시스템 책임 구분을 할 수 없으면 INCONCLUSIVE로 기록한다. collector가 죽어 로그가 없어진 경우 ‘재발 없음’으로 합격시키지 않는다. 코드가 로그를 숨길 수 있으므로 로그 관찰만으로 정상 판단하지 않는다.

계약 통과는 작은 데이터 집합과 관찰 시간에 대한 결과다. 생산 안정성·원인 제거·모든 악성 동작 부재를 증명하지 않는다.

## 6. 신뢰된 verifier와 비신뢰 앱

검증기는 모델이나 앱과 다른 프로세스에서 HTTP 응답을 검사한다. 기대값·계약·판정 함수는 MES/agent가 수정할 수 없는 경로에 둔다. 앱의 출력 `status=resolved`, `tests_passed=true`를 믿지 않는다.

소스 파일 보호는 악성 runtime code가 자기 프로세스의 테스트를 조작하는 것을 완전히 막지 못하므로, 외부 검사와 사람 리뷰를 함께 유지한다. 독립 HTTP 검사도 테스트 요청을 구분하는 악성 응답까지 완전히 배제하지는 못한다. 실제 운영 배포 권한을 부여하지 않는 이유다.

## 7. S1b 시험 경로

S1b는 **팀이 준비한 잘못된 200 fixture/image를 trusted test harness가 주입**하는 검증기 시험이다. `origin=human_injected_negative`로 저장한다. `/tools/*`나 제품 broker에 ‘검사 우회 배포’ 액션을 추가하지 않는다.

DB fixture에서 시험 사건을 `VERIFYING`으로 준비하는 helper는 tests/demo 전용으로만 존재한다. 운영 API에 임의 상태 설정 endpoint를 만들지 않는다. 이 실행을 ‘에이전트가 잘못 고쳤지만 브로커까지 통과함’으로 표시하지 않는다.

## 8. verifier 결과 예시

```json
{
  "verification_id": "VER-001",
  "origin": "human_injected_negative",
  "contract_id": "defect-summary-v1",
  "verdict": "FAIL",
  "reason": "content_mismatch",
  "samples_completed": 1,
  "samples_required": 4,
  "observation_complete": false,
  "failed_assertions": [
    {"case_id": "missing-inspector", "field": "total_defects", "expected": 7, "actual": 0}
  ],
  "resolved_written": false
}
```

반증을 발견하면 FAIL로 조기 종료할 수 있다. 60초 관찰을 끝내지 않았는데 `observation_complete=true`로 표시하지 않는다. PASS만 모든 관찰 조건을 기다린다.


## 9. v4 Issue·work·알림·사례 연결

릴리스 요청의 work_id는 해당 Issue·proposal·PR·승인과 일치해야 한다. Issue에 누가 '완료'라고 썼거나 close 이벤트가 들어와도 verifier 상태를 바꾸지 않는다. merged 상태도 검증 시작 전일 수 있다. PR_READY에는 ‘수정안 준비, 복구 미검증’을 표시한다.

배포/검증 시작은 work WAITING_VERIFICATION과 연결한다. PASS는 incident RESOLVED/work SUCCEEDED, FAIL 또는 INCONCLUSIVE는 ESCALATED/BLOCKED다. 결과 갱신과 notification outbox enqueue를 같은 DB transaction으로 묶는다. 외부 알림 실패 시 verification 결과를 취소하지 않는다.

case builder는 완료된 verification ID·origin·target SHA/image·contract hash·fixture 버전·관찰 시간을 확인하고 노트를 생성한다. PASS만 VERIFIED_SUCCESS, 관찰된 계약 FAIL은 VERIFIED_FAILURE, 수집 공백/불명확은 INCONCLUSIVE다. 각 노트는 적용 범위와 실패 조건을 담는다. 이전 PR-only UNVERIFIED를 조용히 덮어쓰지 않고 후속 revision으로 연결한다.

S1b의 사람이 주입한 결함은 실제 실패 결과로 기록할 수 있으나 origin=human_injected_negative를 유지한다. memory-assisted 시연의 자료로 명시적으로 선택할 수 있어도 ‘agent가 이 실수를 했다’고 표현하지 않는다. 완료 사례에 포함된 이전 정답을 cold_start 평가에 제공하지 않는다.
