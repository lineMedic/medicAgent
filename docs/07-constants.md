# 07. 상수·한도·fixture 값

> 원본: [spec 01 §5](../spec/docs/01-requirements.md), [spec 06 §2·§5](../spec/docs/06-broker-runner.md), [spec 08 §4·§5](../spec/docs/08-release-verification.md), [spec 09 §1·§3](../spec/docs/09-scenarios-evaluation.md), [spec 15 §2](../spec/docs/15-issue-intake-workflow.md), [spec 16 §1](../spec/docs/16-notifications.md).
> 이 값들은 운영 SLA가 아니라 예선용 상한이다. `config/linemedic.toml`의 기본값으로 넣고, 바꾸면 config hash가 달라져 평가 집합을 분리한다(D52, 형식은 D60).

## 1. 에이전트·브로커

| 키 (config) | 값 | 의미 |
|---|---|---|
| `agent.max_concurrent` | 1 | 동시 활성 에이전트 작업 (`one_running_work`) |
| `agent.deadline_seconds` | 240 | 조사 예산. attempt 시작에 고정. 로컬 테스트·모델 재시도·제안 수정 포함 |
| `agent.tool_call_budget` | 15 | 런타임이 실제 실행한 도구 호출 수. 전송 재시도는 별도 계수 |
| `agent.proposal_revisions` | 1 | 거절 후 수정 1회, 원래 deadline 유지 |
| `agent.max_submissions` | 2 | 서로 다른 제출 합산 (DDL `submissions<=2`) |
| `agent.model_transient_retries` | 1 | 외부 변경 없는 조사 단계에서만 |
| `tools.logs.window_minutes` | ±30 | 사건 기준 로그 조회 범위 |
| `tools.logs.max_lines` | 20 | |
| `tools.logs.max_bytes` | 65536 | 64 KiB |
| `tools.metrics.max_minutes` / `max_samples` | 30 / 60 | 설비 지표 |
| `tools.deploys.window_hours` | 24 | |
| `tools.http_timeout_seconds` | 10 | 조회 호출 |
| `proposal.max_bytes` | 131072 | 128 KiB UTF-8 JSON |
| `proposal.summary_max_chars` | 2000 | |
| `proposal.max_evidence_ids` | 20 | |
| `patch.allowed_app_file` | `app/defects.py` | 수정 허용 파일 정확히 1개 |
| `patch.allowed_new_test_glob` | `tests/repro/test_*.py` | 신규 파일 1개 |
| `patch.max_files` | 2 | |
| `patch.max_changed_lines` | 100 | additions + deletions, header 제외 |
| `patch.protected_globs` | `tests/regression/**`, `Dockerfile`, `pyproject.toml`, `requirements*`, `setup.*`, `.github/**`, `pytest.ini`, `conftest.py`, `**/conftest.py`, `tox.ini`, `setup.cfg` | 변경 금지 (hash 보호) |

## 2. runner

| 키 | 값 |
|---|---|
| `runner.network` | `none` |
| `runner.user` | non-root UID/GID, 모든 불필요 capability 제거 |
| `runner.filesystem` | root·checkout read-only, `/tmp`만 크기 제한 tmpfs |
| `runner.cpus` / `memory` / `pids` | 1 / 512 MiB / 64 |
| `runner.stage_timeout_seconds` | 60 |
| `runner.max_log_bytes` | 1048576 (1 MiB) |
| 금지 | privileged, host network, host PID, Docker socket, host home mount |

### pytest 판정 (junitxml + 종료 코드)

| 단계 | 인정 조건 | 그 외 |
|---|---|---|
| R0 | base에서 보호 회귀 전부 passed (exit 0) | 환경 이상 → 중단 |
| R1 재현 | exit 1 **그리고** collected ≥ 1 **그리고** failures ≥ 1 **그리고** errors = 0 (skip·xfail만 있으면 불인정) | exit 0 → `REPRO_NOT_FAILING`; exit 2/3/4/5·수집 오류·OOM·timeout → 재현 증거 아님 |
| R2 통과 | exit 0 **그리고** 새 테스트와 보호 회귀가 모두 passed | → `REGRESSION_FAILED` 등 |

Docker 종료 코드 125/126/127은 Docker·호출 실패로 따로 분류한다.

## 3. 업무 검증 `defect-summary-v1`

| 키 | 값 |
|---|---|
| 요청 경로 | `GET /defects/summary?lot_id=...` (원래 사용자 경로, 내부 함수 직접 호출 금지) |
| 표본 시각 | t = 0, 10, 20, 30초 (모든 case 호출) |
| 관찰 종료 | t = 60초. 그 전에는 PASS 금지 |
| assertion | `strict_response_schema`, `exact_lot_id`, `exact_total_defects`, `exact_by_inspector_mapping`, `sum_groups_equals_total` |
| 숫자 | JSON 정수만. bool·음수·문자열 숫자 거부 |
| observer (core) | t0~t60 로그 스트림 끊김 없음, container ID·image ID 불변, 표본 요청 모두 응답 |
| t0 | 정확한 candidate 컨테이너 실행 + 로그 수집 대상 확인 뒤의 monotonic 시각 |

계약 YAML 원문은 [spec 08 §4](../spec/docs/08-release-verification.md)에 있고, 같은 필드·값의 TOML로 `linemedic/contracts/defect-summary-v1.toml`에 옮긴다(D60).

## 4. fixture (합성 설계값)

### 불량 로트 ([spec 09 §1](../spec/docs/09-scenarios-evaluation.md))

| 로트 | 입력 record의 inspector_id | 총수 | 기대 집계 | 위치·노출 |
|---|---|---:|---|---|
| L3-0927-118 | I-01, I-01, I-01, I-02, I-02, (키 없음), (키 없음) | 7 | I-01=3, I-02=2, 미지정=2 | `l3-mes-api-seed/data/lots/` — 사건 재현 입력 |
| L3-0927-101 | I-01, I-01, I-02 | 3 | I-01=2, I-02=1 | `l3-mes-api-seed/data/lots/` — 정상 회귀 |
| L3-HOLDOUT-201 | I-03, (키 없음), I-03, (키 없음), (키 없음) | 5 | I-03=2, 미지정=3 | 입력·기대값 모두 `linemedic/eval/holdout-defects-v1.json`. 배포 시 입력만 MES에 읽기 전용 mount |

- "키 없음"은 문자열이 아니라 **필드가 없는 JSON object**다. 각 record는 고유 `defect_id`를 가진다.
- `null`, 빈 문자열, 알 수 없는 enum은 이번 정책 범위가 아니다. 임의로 `미지정`에 넣지 않는다.
- 빈 배열 로트는 `total_defects: 0`, `by_inspector: {}`가 정상이다.
- 버그 base: `app/defects.py`가 `row['inspector_id']`로 직접 접근 → 키 없는 record에서 `KeyError`.
- S1b 잘못된 200: HTTP 200, `total_defects: 0` (7 기대). `origin=human_injected_negative`.

### S2-lite 카메라 지표 ([spec 09 §3](../spec/docs/09-scenarios-evaluation.md))

| 카메라 | baseline brightness | observed brightness | observed confidence | 설명 |
|---|---:|---:|---:|---|
| L3-CAM-1 | 100 | 100 | 0.94 | 정상 대조 |
| L3-CAM-2 | 100 | 59 | 0.61 | brightness −41%, confidence 저하 |
| L3-CAM-3 | 100 | 99 | 0.93 | 정상 대조 |

변형 `S2-recent-deploy`: 같은 지표 + 최근 MES 배포 기록(시뮬레이터 ground truth상 원인 아님). 매뉴얼 참조 ID 예: `MANUAL-L3-VISION-4.2`.

## 5. Issue·알림·사례

| 키 | 값 |
|---|---|
| `issue_intake.poll_interval_seconds` | 60 (탐지 SLA 아님) |
| `issue_intake.overlap_seconds` | 120 |
| `issue_intake.per_page` / `max_pages` | 100 / 10. cap 도달 시 잔여 페이지가 있으면 `complete=false` |
| `issue_intake.initial_import` | `observe_only` |
| `issue_intake.auto_start.mode` | `trusted_authors` (숫자 author ID) |
| `issue_intake.auto_start.deny_labels` | `linemedic-ignore`, `needs-human` |
| `issue_intake.closed_issue_policy` | `require_operator` |
| `notifications.required_start_route_id` | `github-issue-primary` |
| `notifications.start_wait_seconds` | 60 |
| `notifications.retry_max_attempts` | 3, 점증 backoff, Retry-After 우선 |
| `memory.top_k` | 최대 5 |
| `memory.snippet_max_chars` | 2000 |
| `memory.query_max_tokens` | 16 (D54) |
| `detector.dedupe` | 같은 fingerprint 60초 안 3회 |

설정 YAML 예시 원문: issue_intake는 [spec 15 §2](../spec/docs/15-issue-intake-workflow.md), notifications는 [spec 16 §1](../spec/docs/16-notifications.md).

## 6. 시간·형식

- 저장: UTC RFC3339 `...Z`. 화면: KST(UTC+9).
- Git SHA: 40자. 로컬 image는 `RepoDigest`가 없으면 local image ID를 기록하고 registry digest처럼 적지 않는다.
- run_id: `^[a-z0-9-]+$` (D50).
