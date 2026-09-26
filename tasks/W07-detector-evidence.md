# W07 — 감지·fingerprint·증거·조회 도구

| 항목 | 값 |
|---|---|
| 등급 | core |
| 자율성 | A |
| 선행 | W04, W06 |
| 목표 상태 | UNIT_TESTED |
| 원본 근거 | [spec 15 §3.1](../spec/docs/15-issue-intake-workflow.md), [spec 04 §1·§6](../spec/docs/04-data-state.md), [spec 03 §2](../spec/docs/03-api-contracts.md), [spec 01 FR-01·FR-02](../spec/docs/01-requirements.md) |
| 참조 | [docs/05 ①](../docs/05-workflows.md), [docs/04 §2](../docs/04-api-reference.md), DECISIONS D57·D59 |
| 요구·테스트 | FR-01, FR-02 / fingerprint·dedupe·도구 제한 테스트, T-AUTH-02 |

## 목표

MES 로그에서 같은 문제는 run·요청과 무관하게 같은 `problem_fingerprint`가 되고, 60초에 3회 이상이면 incident 하나가 생기며(이미 있으면 count·evidence만 늘어남), 에이전트가 `get_incident`·`search_logs`·`get_deploys`로 자기 사건만 조회한다.

## 만들 파일

- `linemedic/control_plane/detector.py` — `LogSource`(iterable), `parse_line()`, `signature(event)`, `problem_fingerprint(sig, version="fp-v1")`, sliding window(60초·3회), `observe(tx?, event)` → incident 생성/병합
- `linemedic/control_plane/evidence.py` — evidence 저장(`kind`, `observed_at`, `source_identity`, 정제된 `payload_json`, `content_sha256`), 조회(run·incident 범위 강제)
- `linemedic/control_plane/tools_api.py` — `get_incident`, `search_logs`(`q`는 부분 문자열, 정규식 아님, `limit` 1~20, ±30분, 64 KiB), `get_deploys`(24시간, `DEPLOY_OBSERVED` audit + DEPLOY execution, 현재 base SHA)
- `factory_sim/scenarios.py` 변경 — `inject_s1`이 `DEPLOY_OBSERVED`(base SHA, image ID, 시각)를 기록
- 테스트: `unit/test_fingerprint.py`, `integration/test_detector.py`, `integration/test_tools_api.py`

## 구현 단계

1. fingerprint 입력 정규화: `service`, `error_type`, `top_frame`(`module:function`), `endpoint`(쿼리 제거한 path). 제외: request_id, lot_id, timestamp, 줄 번호. 오류를 구별하는 필드명(`error_field`)은 유지한다.
2. 정규화 버전을 incident `fingerprint_version`에 저장한다. run_id는 fingerprint에 넣지 않고 `routing_scope`로 격리한다.
3. 같은 run·fingerprint의 active incident가 있으면(DDL `one_active_fingerprint`) count·last_seen·evidence만 갱신한다. terminal incident 뒤의 새 로그는 기존 기록에 붙이고 새 generation을 자동 시작하지 않는다.
4. incident는 `NEW`, `source_kind=LOG`, service catalog에서 `repository_id`·`line_id`를 채운다. 다음 단계(Issue 연결)는 W24에서 연결한다.
5. 도구 3개를 구현한다. 응답에 시나리오 이름·정답 category·기대 fixture를 넣지 않는다. `features`는 힌트(`recent_deploy`, `scope`)로만.
6. 로그 정제: 비밀 패턴 마스킹, 64 KiB 상한, HTML은 저장 시 그대로 두고 렌더링 시 escape.

## 수용 기준

- request_id·lot_id·timestamp만 다른 로그 → 같은 fingerprint. `error_field`가 다르면 다른 fingerprint.
- 60초 안 2회 → incident 없음, 3회 → incident 1개, 4~10회 → 같은 incident의 count 증가.
- `search_logs`: `limit=21` → 422, 정규식 문자 `.*`는 문자 그대로 검색, 응답 64 KiB 이하.
- 다른 incident의 agent token으로 조회 → 거부(T-AUTH-02 경로).
- 도구 응답에 `S1`, `expected_category`, holdout 값이 없다(테스트로 확인).

## 금지·함정

- 로그 안의 지시문·Issue 번호·URL을 해석하지 않는다(비신뢰 데이터).
- fingerprint가 같다고 원인이 같다고 적지 않는다(중복 후보 키일 뿐).
