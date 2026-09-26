# W05 — 독립 업무 verifier와 S1b

| 항목 | 값 |
|---|---|
| 등급 | core |
| 자율성 | A (실제 컨테이너 테스트는 `docker` 마커) |
| 선행 | 1부: W04 / 2부: W06 |
| 목표 상태 | UNIT_TESTED (1부·2부 모두) |
| 원본 근거 | [spec 08 §4~§9](../spec/docs/08-release-verification.md), [spec 09 §1·§4](../spec/docs/09-scenarios-evaluation.md), [spec 01 AC-S1B·FR-08·FR-10](../spec/docs/01-requirements.md) |
| 참조 | [docs/07 §3](../docs/07-constants.md), [docs/03 §2](../docs/03-domain-model.md), DECISIONS D47·D51 |
| 요구·테스트 | FR-08, FR-10, INV-01, AC-S1B / T-VERIFY-01·02·04·05 |

## 목표

verifier가 정상 응답은 t=60초 이후에만 PASS로, HTTP 200이지만 틀린 집계는 FAIL/`content_mismatch`로, 관찰이 끊기거나 대상이 바뀌면 INCONCLUSIVE로 판정한다. 2부에서 그 결과가 DB에 저장되고 incident 전이로 이어진다.

## 1부 — 판정 엔진 (W04 이후)

### 만들 파일

- `linemedic/contracts/defect-summary-v1.toml` — [spec 08 §4](../spec/docs/08-release-verification.md) YAML 원문과 같은 필드·값을 TOML로 옮긴 것(D60). 단위 테스트가 파싱 결과를 spec 원문에서 손으로 옮긴 기대 dict와 필드별로 비교한다. `contract_sha256`는 파일 바이트의 SHA-256
- `linemedic/control_plane/verifier.py` — `verify(target, contract, clock, http, observer) -> VerificationResult`
- `linemedic/control_plane/observer.py` — `Observer`: 대상 container의 로그 스트림을 t0부터 연속으로 읽고(끊기면 gap 기록), 표본 시점마다 container ID·image ID를 inspect, 같은 fingerprint의 오류 재발 감지
- `linemedic/integrations/docker.py` — `DockerPort`(`inspect`, `logs_follow`, `run`, `stop`, `build`), `CliDocker`(고정 argv), `FakeDocker`
- `linemedic/factory_sim/negative/` — S1b 잘못된 200 구현(로트 118에 `total_defects: 0`), 전용 image 레시피
- `make verify-negative RUN_ID=` — trusted harness: S1b image 기동 → verifier 실행 → 결과 JSON 출력 (1부에서는 DB 없이 `runs/<run_id>/verifications/`에 저장)
- 테스트: `linemedic/tests/unit/test_verifier.py`, `linemedic/tests/integration/test_verifier_docker.py`(docker)

### 판정 규칙

- `t0` = 대상 컨테이너 실행 + 로그 수집 대상 확인 뒤의 monotonic 시각.
- t=0·10·20·30초에 contract의 **모든 case**를 원래 경로 `GET /defects/summary?lot_id=`로 호출한다. holdout case는 `eval/holdout-defects-v1.json`에서 기대값을 읽는다.
- assertion 5종: strict schema(추가 key 거부), lot_id 정확, total 정확, by_inspector 정확 매핑, 그룹 합 = total. 숫자는 JSON 정수만(bool·음수·문자열 숫자 거부).
- 반증(응답 불일치, 업무 오류, 같은 오류 재발)을 보면 FAIL로 **조기 종료할 수 있다**. 이때 `observation_complete=false`.
- PASS는 t=60초까지 기다린 뒤, 모든 표본 통과 + 재발 없음 + 로그 스트림 끊김 없음 + container·image·fixture 불변일 때만.
- 수집 중단, timeout 원인 불명, identity 변경, 증거 누락은 INCONCLUSIVE.
- 결과 형식은 [spec 08 §8](../spec/docs/08-release-verification.md) 예시 필드를 모두 포함(`verification_id, origin, contract_id, verdict, reason, samples_completed, samples_required, observation_complete, failed_assertions, resolved_written`).

### 수용 기준 (1부)

- T-VERIFY-01: FakeClock으로 정상 응답 → t=59에는 판정 없음, t=60 이후 PASS.
- T-VERIFY-02: total 0 / 다른 lot_id / 추가 key / `"7"` / `true` / `-1` → FAIL, reason `content_mismatch`.
- T-VERIFY-04: 4회 표본 통과 후 t=45에 같은 오류 로그 → FAIL.
- T-VERIFY-05: t=25에 image ID 변경 → INCONCLUSIVE. fixture hash 변경 → INCONCLUSIVE.
- core observer: t=40에 로그 스트림 종료 → INCONCLUSIVE("재발 없음"으로 PASS 금지).
- docker: `make verify-negative`가 실제 S1b 컨테이너에 대해 FAIL/`content_mismatch`.

## 2부 — 저장과 전이 (W06 이후)

### 만들 파일·변경

- `verifier.py`에 `persist_result(tx, ...)`: `verifications` INSERT(origin, contract_id·sha, started/ended, result_json), 그리고 **verifier actor로만** incident `VERIFYING → RESOLVED`(PASS) 또는 `→ ESCALATED`(FAIL·INCONCLUSIVE), work 결합 전이, 결과 알림 outbox intent(`RECOVERY_VERIFIED`/`RECOVERY_NOT_VERIFIED`)를 같은 트랜잭션에서 기록
- `linemedic/tests/helpers/demo_states.py` — 테스트·demo 전용으로 incident를 `VERIFYING`에 준비하는 helper. 운영 API로 노출하지 않는다
- `make verify-negative`를 DB 기록 방식으로 갱신: `origin=human_injected_negative`, incident ESCALATED

### 수용 기준 (2부)

- verifier 모듈 밖에서 `RESOLVED` 전이를 시도하면 거부된다(T-STATE-02와 함께).
- S1b 실행 후 incident는 ESCALATED, verification은 FAIL, `resolved_written=false`.
- S1b 결과가 agent 성능 집계 대상이 아님을 origin으로 구분한다.

## 완료 증거

테스트 ID별 결과, `make verify-negative` 출력 경로, contract sha256.

## 금지·함정

- 앱의 `/version`이나 응답의 `status=resolved`를 믿지 않는다. identity는 host inspect로 확인한다.
- 내부 함수를 직접 호출해 검증하지 않는다. 원래 HTTP 경로만 쓴다.
- `/tools/*`나 broker에 "검사 우회 배포" 액션을 만들지 않는다. S1b는 trusted harness 전용이다.
- 60초 관찰 전에 `observation_complete=true`를 쓰지 않는다.
