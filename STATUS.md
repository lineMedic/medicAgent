# STATUS — LineMedic v4 구현 진행표

> **개발 진행 상태와 완료 보고의 유일한 원본 — 직접 편집한다.** 착수·완료·차단·재개 때 아래 표와 완료 보고를 함께 갱신한다([D62](DECISIONS.md)). 작업 범위·선행·게이트·수용 기준은 [tasks/](tasks/README.md) 카드에서 확인한다.
> 상태 enum과 증거 규칙은 [docs/11](docs/11-definition-of-done.md)에 있다. 시각은 UTC로 적는다(예: `2026-09-27T05:10Z`). 상태를 올리기 전에 선행·해당 실행의 사람 승인·원본 증거를 직접 확인한다. 비밀값·수신 주소를 기록하지 않는다.
> 초기값은 원본 [spec 10 §2](spec/docs/10-delivery-plan.md)과 같이 전부 `NOT_CHECKED`다. 이 문서 패키지를 작성한 것만으로는 어떤 W도 완료되지 않았다.

## 현재 작업

| 카드 | 시작 시각 | 진행 메모 |
|---|---|---|
| (없음) | | |

## 다음 작업

[AGENTS.md §2](AGENTS.md)의 선택 조건에 따른 다음 카드: **W08** ([tasks/W08-s2-lite-fixtures.md](tasks/W08-s2-lite-fixtures.md), S2-lite 카메라 지표·설비·매뉴얼 — 선행 W06·W07 충족, 게이트 없이 목표 상태까지 가능). W00은 G1, W01은 G6, W02 live는 G3·G4·G5, W03 live·시드 push는 G2·G10 대기다.

## 작업표

| # | 카드 | 목표 상태 | 현재 상태 | 증거 (명령·커밋·경로) | 게이트·차단 | 갱신 |
|---|---|---|---|---|---|---|
| 1 | B00 | UNIT_TESTED | UNIT_TESTED | `make test` → 38 passed, `make lint` → PASS, `python -m linemedic.cli doctor` → exit 1 (env NOT_CONFIGURED), 새 clone `make setup`·`make test`·`make lint` PASS, 커밋 `cc2766b6c8fc2ca221893a10fe2c2602745ca4aa` | | 2026-09-27T02:27Z |
| 2 | W00 | LIVE_VERIFIED | BLOCKED | | BLOCKED_ON_HUMAN: G1 — 데모 호스트 확정·`DEMO_HOST_ID` / 확인: 확정 호스트에서 `make host-manifest > evidence/host-manifest.json` | 2026-09-27T02:27Z |
| 3 | W01 | LIVE_VERIFIED | BLOCKED | 기록 양식 `evidence/contest-conditions.md`(R1~R5 상태 표·답변 표·공식 페이지 관찰), 커밋은 W01 완료 보고 참조 | BLOCKED_ON_HUMAN: G6 — 주최 측 문의 발송·답변 원문 / 확인: `evidence/contest-conditions.md` §2 답변 표 | 2026-09-27T03:15Z |
| 4 | W02 | LIVE_VERIFIED | UNIT_TESTED (live: BLOCKED_ON_HUMAN G3·G4·G5) | `make test` → 46 passed(N01 스크립트 단위 테스트 8개 포함), `make test-live` → 1 skipped(NOT_CONFIGURED), N01 스크립트 키 없이 실행 → 종료 코드 2(NOT_CONFIGURED). 스파이크 evidence 없음(미실행) | BLOCKED_ON_HUMAN: G3 — `.env`에 NVIDIA_BASE_URL·NVIDIA_MODEL_ID·NVIDIA_API_KEY / 확인: N01 스크립트 PASS. G4 — N02 결과로 runtime 결정. G5 — OpenShell 설치 후 N03·N04·N09·N10 | 2026-09-27T03:23Z |
| 5 | W03 | LIVE_VERIFIED | UNIT_TESTED (live: BLOCKED_ON_HUMAN G2·G10) | `make test` → 63 passed(GitHub 점검·보호 시험·doctor github 단위 테스트 17개 포함), 점검·보호 시험 스크립트 키 없이 실행 → 종료 코드 2(NOT_CONFIGURED), `make doctor`의 github 항목 NOT_CONFIGURED. live 점검·쓰기 시험·시드 push는 미실행 | BLOCKED_ON_HUMAN: G2 — 데모 repo·봇·리뷰어·`baseline/*` 보호·squash·credential / 확인: `python -m linemedic.scripts.github_setup_check --reviewer <계정> --output evidence/github-setup-check.json`. G10 + 사용자 허락 — `github_protection_probe --confirm-write`. 시드 push는 W04 이후 | 2026-09-27T04:02Z |
| 6 | W04 | UNIT_TESTED | UNIT_TESTED | `make test` → 79 passed(W04 단위 테스트 16개 포함), `make test-docker` → 1 passed(실제 컨테이너: 로트 118 500×3·KeyError 로그, 101 200, 격리·egress 차단 확인), `make mes-image` → image `sha256:425755201561179ca1cf1ee1eccf03ef2559a8d556a9ce0b36b4a32968d5bce0`, base `python@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f`, 시드 커밋 `19045b62f292dedff24529cab505e6d86a91ed8c`(tree `e6718ce7deb861efd2d4916cbd27ef3078c621e3`, 결정적) | | 2026-09-27T04:19Z |
| 7 | W05 (1부) | UNIT_TESTED | UNIT_TESTED | `make test` → 154 passed(W05 단위 테스트 74개 + W04 회귀 1개 포함), `make test-docker` → 3 passed(실제 S1b 컨테이너 FAIL/content_mismatch, 실제 KeyError 로그 재발 signature), `make verify-negative RUN_ID=r-20260927-050621-a8c3` → 종료 코드 0, `VER-588F634C683A` FAIL/content_mismatch, 표본 1/4, observation_complete=false, resolved_written=false, 결과 `runs/r-20260927-050621-a8c3/verifications/VER-588F634C683A.json`(git 제외 경로), contract_sha256 `0334df2662cdb121064bdc6e34b016980b497afb17d9b53b916e03d0c0bfc87f` | 2부는 W06 뒤 | 2026-09-27T05:07Z |
| 8 | W06 | UNIT_TESTED | UNIT_TESTED | `make test` → 547 passed(W06 테스트 393개), `make lint` → PASS, `make test-docker` → 3 passed, DDL 제약 19건 + `PRAGMA foreign_key_check` 빈 결과(FTS5 1건은 W27), T-AUTH-01~03·T-IDEM-01·02·T-STATE-02·03 PASS, `make run-new` → run `r-20260927-054424-94f9`, `runs/linemedic.db`(git 제외), config_hash `3b9c3d0150ebc4ac53acf0de7e95f729ff2418614bd669d511fca665667e6361` | | 2026-09-27T05:46Z |
| 9 | W05 (2부) | UNIT_TESTED | UNIT_TESTED | `make test` → 567 passed(2부 테스트 20개 포함), `make lint` → PASS, `make test-docker` → 3 passed(실제 S1b → DB에 incident ESCALATED·verification FAIL), `make verify-negative RUN_ID=r-20260927-054424-94f9` → 종료 코드 0, `VER-B4BE5C22EA1F` FAIL/content_mismatch·origin human_injected_negative·resolved_written=false, `INC-6878BEAECCC1` VERIFYING → ESCALATED(`VERIFICATION_FAILED`, 주체 verifier), `runs/linemedic.db`·`runs/r-20260927-054424-94f9/verifications/VER-B4BE5C22EA1F.json`(git 제외) | | 2026-09-27T05:59Z |
| 10 | W07 | UNIT_TESTED | UNIT_TESTED | `make test` → 641 passed(W07 테스트 74개 포함), `make lint` → PASS, `make test-docker` → 4 passed(실제 S1 로그 → 사건 1개 → 조회 도구), `make scenario-s1`·`make detect-once RUN_ID=r-20260927-054424-94f9` → `DEPLOY_OBSERVED` 기록, 사건 `INC-885B28A026C0` NEW(count 3·증거 3·line L3·fp-v1), 로그 5줄 `runs/r-20260927-054424-94f9/logs/mes-api.jsonl`(git 제외), 정리 뒤 컨테이너·network 0개 | | 2026-09-27T06:22Z |
| 11 | W08 | UNIT_TESTED | NOT_CHECKED | | | |
| 12 | W09 | UNIT_TESTED | NOT_CHECKED | | | |
| 13 | W22 | LIVE_VERIFIED | NOT_CHECKED | | G2·G10 | |
| 14 | W23 | LIVE_VERIFIED | NOT_CHECKED | | G2 | |
| 15 | W24 | LIVE_VERIFIED | NOT_CHECKED | | G2·G10 | |
| 16 | W25 | UNIT_TESTED | NOT_CHECKED | | | |
| 17 | W26 | LIVE_VERIFIED | NOT_CHECKED | | G2·G10 (G12 선택) | |
| 18 | W10 | UNIT_TESTED | NOT_CHECKED | | | |
| 19 | W11 | LIVE_VERIFIED | NOT_CHECKED | | G2·G10 | |
| 20 | W12 | LIVE_VERIFIED | NOT_CHECKED | | G7·G8 | |
| 21 | W13 | LIVE_VERIFIED | NOT_CHECKED | | G2·G7·G8·G10 | |
| 22 | W27 | UNIT_TESTED | NOT_CHECKED | | | |
| 23 | W14 | LIVE_VERIFIED | NOT_CHECKED | | G3·G4 | |
| 24 | W15 | LIVE_VERIFIED | NOT_CHECKED | | G5·G7·G8 | |
| 25 | W16 | LIVE_VERIFIED | NOT_CHECKED | | G5 | |
| 26 | W28 | LIVE_VERIFIED | NOT_CHECKED | | G3~G5 | |
| 27 | W17 | LIVE_VERIFIED | NOT_CHECKED | | G5 | |
| 28 | W18 | UNIT_TESTED | NOT_CHECKED | | | |
| 29 | W19 | LIVE_VERIFIED | NOT_CHECKED | | G2 | |
| 30 | W20 | LIVE_VERIFIED | NOT_CHECKED | | 전체 | |
| 31 | W29 | LIVE_VERIFIED | NOT_CHECKED | | 전체·G9 | |
| 32 | W21 | LIVE_VERIFIED | NOT_CHECKED | | 사람 | |
| 33 | H03~H07 | UNIT_TESTED | NOT_CHECKED | | core 완료 후 | |

## 사람 게이트

`OPEN`은 아직 준비·승인이 완료되지 않은 상태다. 요청하면 `REQUESTED`와 요청 시각·절차를 적고, 사람이 완료를 알려 주면 `DONE`과 완료 시각·승인 범위·확인 근거를 기록한다. G7·G8은 run마다 확인하며, 이전 run의 완료가 다음 run을 승인하지 않는다.

| ID | 내용 | 상태 (OPEN / REQUESTED / DONE / DECLINED) | 요청 내용·요청 시각 | 사람이 알려 준 값 (비밀 제외) | 완료 시각 |
|---|---|---|---|---|---|
| G1 | 데모 호스트 확정 | REQUESTED | 2026-09-27T02:10Z — 평가·영상용 호스트 1대(OS·arch·메모리·디스크) 결정, OpenShell Support Matrix와 대조. 현재 개발 Mac은 OpenShell 미설치 | | |
| G2 | GitHub 조직·repo·봇·리뷰어·보호 규칙·trusted author ID | REQUESTED | 2026-09-27T02:10Z — ① 조직에 `l3-mes-api` 생성 ② 봇 계정/App(해당 repo만 metadata·Issues·PR·contents) ③ 봇 아닌 리뷰어 ④ `baseline/*` 보호(리뷰 1, 최신 변경 승인, 봇 직접 push 금지) ⑤ squash만 ⑥ 자동 처리 작성자 숫자 ID. 조직 관리자 필요 | | |
| G3 | NVIDIA 키·모델 | REQUESTED | 2026-09-27T02:10Z — build.nvidia.com 키 발급 후 `.env`에 `NVIDIA_API_KEY` 직접 입력, `NVIDIA_BASE_URL`·`NVIDIA_MODEL_ID` 후보 알려 주기 | | |
| G4 | runtime 선택 (OpenClaw/NemoClaw vs NAT) | OPEN | | | |
| G5 | OpenShell 설치·정책 | REQUESTED | 2026-09-27T02:10Z — G1 호스트에 OpenShell 설치·버전 고정, effective policy 확인 방법과 정책 schema 문서 위치 알려 주기 | | |
| G6 | 대회 조건 R1~R5 문의 | REQUESTED | 2026-09-27T02:10Z — spec 12 §2 문안을 주최 측에 발송, 답변 원문(확인일·질문·답변·출처·확인자) 전달 | | |
| G7 | PR 리뷰·머지 (run마다) | OPEN | | | |
| G8 | 배포 승인 실행 (run마다) | OPEN | | | |
| G9 | memory snapshot 선택 | OPEN | | | |
| G10 | GitHub 쓰기 활성화 (shadow 해제) | OPEN | | | |
| G11 | 체크포인트 KST 시각 | REQUESTED | 2026-09-27T02:10Z — V4-CP0~CP5 목표 KST, 코드 동결·평가 시작·내부 제출 시각 결정 | | |
| G12 | 메일 채널 선택 여부 | OPEN | | | |

## 체크포인트

상태는 `NOT_CHECKED / NOT_MET / MET`이며, `MET`에는 실제 완료 근거를 기록한다.

| CP | 완료 조건 ([docs/08 §5](docs/08-task-plan.md)) | 목표 KST (G11) | 상태 | 증거 |
|---|---|---|---|---|
| V4-CP0 | W 상태·남은 시간·repo·author·channel 확정 | 미입력 | NOT_CHECKED | |
| V4-CP1 | 모델 없이 Issue 신규/기존 연결·중복 work 방지 | 미입력 | NOT_CHECKED | |
| V4-CP2 | 실제 시작 알림 receipt → 사람 제안 통합, blocker 알림 | 미입력 | NOT_CHECKED | |
| V4-CP3 | 실제 sandbox agent가 Issue 단위 PR/초안 생성 | 미입력 | NOT_CHECKED | |
| V4-CP4 | 결과 저장·분리·검색 → model context | 미입력 | NOT_CHECKED | |
| V4-CP5 | 실제 검증·S4~S7 회귀·cold/memory 구분·증거 보존 | 미입력 | NOT_CHECKED | |

## 대회 조건 (W01)

상태는 `UNCONFIRMED / PROVISIONAL / CONFIRMED / DENIED`이며, 확인·불충족 판정에는 G6 답변의 출처를 기록한다.

| ID | 상태 | 근거 |
|---|---|---|
| R1 Skill API | UNCONFIRMED | `evidence/contest-conditions.md` — 답변 대기 |
| R2 NeMo Framework/Microservices | UNCONFIRMED | `evidence/contest-conditions.md` — 답변 대기 |
| R3 심사 항목 | PROVISIONAL (제공 자료 기준, 최종 원문 확인 필요) | `evidence/contest-conditions.md` — 답변 대기 |
| R4 데모·코드·개별 신청 | UNCONFIRMED | `evidence/contest-conditions.md` — 답변 대기 |
| R5 공식 마감 | UNCONFIRMED (원안의 더 이른 일정을 보수적으로 사용) | `evidence/contest-conditions.md` — 답변 대기. 공식 페이지 표기가 9/28과 10/1로 엇갈림(§3 관찰) |

## 완료 보고 기록

카드를 끝내거나 멈출 때마다 [docs/11 §3](docs/11-definition-of-done.md) 양식으로 이 절에 직접 추가한다(최신이 위). 카드 밖의 문서 변경은 제품 카드 완료와 구분해 기록한다.

### W07 완료 보고 (2026-09-27T06:22Z)

- 상태: UNIT_TESTED (카드 목표 도달). docker 시험과 실제 CLI 흐름(`scenario-s1` → `detect-once`)도 로컬에서 통과했다
- 변경 파일:
  - `linemedic/control_plane/detector.py`: `parse_line`(엄격한 JSON 객체만), `signature`(오류 줄만, service는 source 기준), `problem_fingerprint`(fp-v1), 60초 3회 창, 사건 생성·병합·terminal 흡수, `settings_for_run`
  - `linemedic/control_plane/evidence.py`(정제·크기 상한·run·incident 범위 조회), `log_store.py`(정제 로그 JSONL 보관, `MemoryLogStore`), `redaction.py`(비밀·평가 전용 식별자 가림), `deploys.py`(`DEPLOY_OBSERVED` 기록·배포 기록 조회)
  - `linemedic/control_plane/tools_api.py`: `GET /tools/incidents/{id}`, `/logs`, `/deploys`. `auth.py`는 agent 범위에 현재 attempt·RUNNING work 조건을 더했다. `app.py`는 tools 라우터·로그 설정·모르는 query 거부
  - `linemedic/factory_sim/scenarios.py`: `inject_s1`이 제어 DB에 `DEPLOY_OBSERVED`를 남긴다. `linemedic/integrations/docker.py`: `logs_once`
  - `linemedic/cli.py`(`detect-once`, `scenario-s1 --db`), `Makefile`(`detect-once`), `config/linemedic.toml`·`common/config.py`(`[services.mes-api].line_id`), `common/clock.py`(`from_rfc3339`), DECISIONS.md·ADR.md(D72)
  - 테스트: `unit/test_fingerprint.py`(37), `integration/test_detector.py`(16), `integration/test_tools_api.py`(18), `integration/test_detector_docker.py`(docker 1), `unit/test_auth.py`(attempt·work 조건 4개 추가·T-AUTH-02 준비 갱신), 도우미 `seed_running`
- 실행 (로컬 개발 Mac — 데모 호스트 아님):
  - `make test` → 641 passed, 5 deselected / `make lint` → PASS / `make test-docker` → 4 passed
  - `make scenario-s1 RUN_ID=r-20260927-054424-94f9` → 요청 500×3·200×1, `DEPLOY_OBSERVED`(image `sha256:425755201561179ca1cf1ee1eccf03ef2559a8d556a9ce0b36b4a32968d5bce0`, base SHA null — `BASELINE_COMMIT` 미설정)
  - `make detect-once RUN_ID=r-20260927-054424-94f9` → 5줄·오류 3줄, 사건 `INC-885B28A026C0` NEW(source LOG, count 3, 증거 3, line L3, repository_id 0, fp-v1). 감사 `INCIDENT_DETECTED`(detector). `foreign_key_check` 빈 결과. 확인 뒤 이 run의 S1 컨테이너·network를 정확한 이름으로 지웠다
  - 변이 확인: error_field 제외, 창 경계 배타, terminal 사건 무시, 정규식 검색, 평가 식별자 가림 제거, work 상태 조건 제거, 64 KiB 상한 제거를 각각 넣으면 테스트가 실패했다. 확인 뒤 원래 코드로 되돌렸다
- 수용 기준:
  - request_id·lot_id·timestamp(·줄 번호·쿼리)만 다른 로그 → 같은 fingerprint, error_field가 다르면 다른 fingerprint: PASS
  - 60초 안 2회 → 사건 없음, 3회 → 사건 1개, 4~10회 → 같은 사건 count 증가: PASS(60초 경계 포함·창 밖 오류 제외도 확인)
  - `search_logs`: limit=21 → 422, `.*`는 문자 그대로 검색, 응답 64 KiB 이하: PASS
  - 다른 사건의 agent token으로 조회 → 없는 사건과 같은 404(T-AUTH-02, 세 도구 모두): PASS
  - 도구 응답에 `S1`·`expected_category`·holdout 값이 없음: PASS(holdout 요청 로그 줄은 `[REDACTED:eval]`)
- 판단:
  - D72: fingerprint 배열 인코딩, source 기준 service, 관찰 시계 창, terminal 사건 흡수, 로그 JSONL 보관, 평가 전용 식별자 수집 시점 가림, `/tools`는 RUNNING attempt만, search_logs 세부
  - W06의 T-AUTH-02 시험은 PR_OPENED 사건을 agent가 읽는 준비였는데, attempt 조건을 더하면서 RUNNING work 준비(`seed_running`)로 바꿨다
- 증거: 커밋은 이 보고를 포함한 W07 커밋. DB·로그 파일은 git 제외 경로 `runs/` 아래에 있다
- 남은 일·위험:
  - `detect-once`는 커서가 없어 같은 컨테이너에 두 번 돌리면 같은 줄을 다시 센다. 상시 감시(`docker logs --follow`)는 W13
  - repository_id는 G2 전이라 0이다. Issue 연결(W24)에는 실제 ID가 필요하다
  - get_incident의 memory 필드는 W28, 설비 사건(S2-lite) 감지는 W08
- 다음 카드: W08

### W05 (2부) 완료 보고 (2026-09-27T05:59Z)

- 상태: UNIT_TESTED (2부 목표 도달). W05는 1부·2부 모두 목표 상태다. docker 시험과 실제 `make verify-negative`의 DB 기록도 로컬에서 통과했다
- 변경 파일:
  - `linemedic/control_plane/verifier.py`: `persist_result`(한 트랜잭션에서 `verifications` INSERT → verifier 주체 전이 → `RECOVERY_VERIFIED`/`RECOVERY_NOT_VERIFIED` 알림 intent → 감사), `agent_performance_verifications`(origin=agent_release만), `verify`가 판정 중 예상하지 못한 예외를 INCONCLUSIVE/`verifier_error`로 끝냄(`VerificationRun.abort`)
  - `linemedic/tests/helpers/demo_states.py`: 테스트·demo 전용 VERIFYING 시험 사건 준비(감사 actor `trusted_harness`). 운영 API에서 쓰지 않음을 정적 검사
  - `linemedic/factory_sim/negative/harness.py`: 제어 DB의 활성 run 확인 → MES 준비 후 시험 사건 준비 → verifier → `persist_result` → 결과 파일. `expected_outcome`이 사건 ESCALATED까지 확인
  - `linemedic/cli.py`(`verify-negative --db --config`, 제어 DB 없으면 `make run-new` 안내), `Makefile`(`verify-negative` 안내 문구), DECISIONS.md·ADR.md(D71)
  - 테스트: `integration/test_verifier_persist.py`(15), `unit/test_verifier.py`(harness DB 기록·활성 run·verifier_error·CLI 4개 추가·갱신), `integration/test_verifier_docker.py`(DB 기록 확인)
- 실행 (로컬 개발 Mac — 데모 호스트 아님):
  - `make test` → 567 passed, 4 deselected / `make lint` → PASS / `make test-docker` → 3 passed
  - `make verify-negative RUN_ID=r-20260927-054424-94f9`(W06의 `make run-new`로 만든 활성 run) → 종료 코드 0. `VER-B4BE5C22EA1F`: FAIL/content_mismatch, 표본 1/4, observation_complete=false, resolved_written=false, origin human_injected_negative. `INC-6878BEAECCC1`: VERIFYING → ESCALATED(reason `VERIFICATION_FAILED`). 감사: `RUN_CREATED`(operator) → `DEMO_STATE_PREPARED`(trusted_harness) → `INCIDENT_TRANSITION`(verifier) → `VERIFICATION_RECORDED`(verifier). `foreign_key_check` 빈 결과, 이 run ID의 컨테이너·network 0개
  - 변이 확인: FAIL도 RESOLVED로 기록, 알림 intent 제거, 저장본 `resolved_written` 미갱신, `verify`가 예외를 그대로 올림을 각각 넣으면 테스트가 실패했다(4·4·1·2개). 확인 뒤 원래 코드로 되돌렸다
- 수용 기준 (2부):
  - verifier 모듈 밖의 RESOLVED 전이 거부: T-STATE-02(비 verifier 주체 8개 거부)와 정적 검사(제품 모듈 중 `Actor.VERIFIER`를 쓰는 곳은 `verifier.py`뿐) PASS
  - S1b 실행 후 incident ESCALATED, verification FAIL, resolved_written=false: FakeDocker 시험·docker 시험·실제 `make verify-negative` 모두 PASS
  - S1b 결과를 origin으로 agent 성과 집계에서 구분: `agent_performance_verifications`가 human_injected_negative·manual_integration을 뺌 PASS
- 판단:
  - D71: 저장·전이·알림·감사를 한 트랜잭션으로 묶고, ESCALATED reason을 FAIL `VERIFICATION_FAILED`·INCONCLUSIVE `OBSERVATION_INCONCLUSIVE`로 정했다. work가 없는 시험 사건은 알릴 Issue가 없어 알림 intent를 넣지 않는다. 판정 중 예외는 PASS나 미기록이 아니라 INCONCLUSIVE로 남긴다
  - spec 08 §7대로 시험 사건 준비 도우미를 테스트 도우미 패키지에 두었고, trusted harness가 그것을 import한다(대가로 기록)
- 증거: 커밋은 이 보고를 포함한 W05 2부 커밋. DB와 결과 파일은 git 제외 경로 `runs/` 아래에 있다
- 남은 일·위험: W26 전에는 RECOVERY_* 알림 intent가 PENDING으로 쌓인다. 결과 알림 본문·발송은 W26, case note 연결은 W27
- 다음 카드: W07

### W06 완료 보고 (2026-09-27T05:46Z)

- 상태: UNIT_TESTED (카드 목표 도달). 외부 쓰기 없음
- 변경 파일:
  - `linemedic/control_plane/migrations/0001_init.sql`: spec 04 §5 DDL을 구분선 사이에 원문 그대로 두고 `schema_migrations`만 더했다
  - `linemedic/control_plane/store.py`: `connect`(foreign_keys·WAL·busy_timeout·autocommit), `migrate`(파일마다 한 트랜잭션, 모르는 번호 거부), `Store.tx()`(`BEGIN IMMEDIATE`, SQLITE_BUSY 3회 재시도 후 `StoreBusy`), `Store.read()`(query_only), `cas_update`(허용 목록 테이블·열, 0행이면 `StateConflict`). `Tx`는 SQL 실행과 시각만 가진다
  - `linemedic/control_plane/state.py`: docs/03 incident·work 전이 표와 결합 표를 데이터로 두고 `transition_incident`·`transition_work`·`coupled_transition`(결합 표 밖 조합·다른 incident의 work 거부, 알림 종류 반환). 주체는 `Actor` 열거형만 받고 전이마다 감사 기록
  - `linemedic/control_plane/audit.py`(비밀을 가려 `audit_events` 기록), `idempotency.py`(NEW/REPLAY/CONFLICT/IN_FLIGHT, 재시작 때 RECEIVED → UNKNOWN), `auth.py`(token hash 등록부, agent token 발급·attempt 폐기, operator 역할, 사건 범위 가드 `load_visible_incident`), `errors.py`(외피·코드 → HTTP·고정 메시지)
  - `linemedic/control_plane/app.py`: app factory, 라우팅 전 prefix 인증 가드, body 처리(크기 → `loads_strict` → pydantic strict·extra=forbid), `Idempotency-Key` 필수, 예외 → 오류 외피, 공개 문서 경로 없음
  - `linemedic/control_plane/ops_api.py`: `GET /ops/incidents/{id}`, `POST /ops/incidents/{id}/escalate`(D70)
  - `linemedic/control_plane/notifications/outbox.py`: 알림 intent `enqueue`(PENDING만, 발송은 W26), `linemedic/control_plane/runs.py`(`create_run`·manifest·`new_run`), `linemedic/common/sanitize.py`(비밀 마스킹·멘션 무력화·허용 repo 밖 URL 비활성화·HTML 이스케이프)
  - `linemedic/cli.py`(`run-new`), `Makefile`(`run-new`), DECISIONS.md·ADR.md(D68~D70)
  - 테스트: `integration/test_ddl_constraints.py`(21), `unit/test_state_transitions.py`(261), `unit/test_auth.py`(22), `integration/test_idempotency.py`(15), `unit/test_api_contract.py`(41), `unit/test_sanitize.py`(17), `integration/test_store.py`(16), 도우미 `linemedic/tests/helpers/`(테스트 전용 행 생성·API 준비)
- 실행 (로컬 개발 Mac — 데모 호스트 아님):
  - `make test` → 547 passed, 4 deselected / `make lint` → PASS / `make test-docker` → 3 passed
  - `make run-new` → run `r-20260927-054424-94f9`, routing scope `eval:r-20260927-054424-94f9`, `runs/linemedic.db`(migration 1, 활성 run 1개, `foreign_key_check` 빈 결과, 감사 `RUN_CREATED`)
  - 변이 확인: `/ops` prefix 가드 제거, broker에게 RESOLVED 허용, 멱등 재반환 제거, 본문 hash 비교 제거, extra 필드 허용, 스트림 크기 제한 제거, CAS 상태 조건 제거를 각각 넣으면 테스트가 실패했다. prefix 가드 제거는 처음에 endpoint 역할 검사 때문에 통과해서, 라우트가 없는 `/ops` 경로의 403 테스트를 더한 뒤 실패를 확인했다. 확인 뒤 원래 코드로 되돌렸다
- 테스트 ID별 결과:
  - DDL 제약: PACKAGE-VALIDATION §3의 1~18·20 PASS, 0001이 spec 04 §5와 글자 단위로 같음. 19(FTS5 필터 질의)는 `case_search`를 만드는 W27에서 한다
  - T-STATE-02 PASS: verifier가 아닌 8개 주체의 RESOLVED·SUCCEEDED 전이와 결합 전이 모두 거부
  - T-STATE-03 PASS: WORK_ORDER_DRAFTED에서 모든 도착 상태·모든 주체 거부
  - 전이 표 전수: incident 10×10·work 11×11 쌍을 9개 주체로 시험(표 안의 허용 주체만 성공, version +1, 감사 1건)
  - T-AUTH-01 PASS: agent token으로 `/ops/incidents/*` 조회·중단 → 403, DB·요청 기록·감사·알림 변화 없음. 라우트 없는 `/ops` 경로도 403. W12에서 `/ops/releases`로 다시 확인한다
  - T-AUTH-02 PASS: agent 범위 가드가 같은 run의 다른 사건·다른 run·없는 사건·형식 오류를 모두 같은 404로 거부. W07의 `/tools` endpoint로 다시 확인한다
  - T-AUTH-03 PASS: body의 `actor`·`status`·`role`·`model`·`policy_version`·`X-Operator` → 422, DB 변화 없음
  - T-IDEM-01 PASS: 같은 키·같은 본문 두 번 → 같은 응답, 전이·감사·알림 1회. key 순서·공백만 다른 본문도 같은 요청
  - T-IDEM-02 PASS: 같은 키·다른 본문 → 409 `IDEMPOTENCY_CONFLICT`, DB 변화 없음. RECEIVED/UNKNOWN → 409 `STATE_CONFLICT`(재실행 없음)
  - 그 밖: `schema_version: "linemedic.v2"` → 422, 오래된 `expected_incident_version` → 409 `STATE_CONFLICT`(현재 상태·version), 크기 초과 → 413(Content-Length·스트림 둘 다), 중복 key·NaN·JSON 아님 → 422, SQLITE_BUSY → 503, 오류 응답에 token·입력 비밀 없음
- 판단:
  - D68: DB 위치·migration 기록·재시도 한도·요청마다 새 연결
  - D69: 전 경로 인증과 라우팅 전 prefix 가드, token hash 메모리 등록부(P0 한 프로세스), 범위 밖·없음 동일 404, 제안 외 형식 오류 코드 `INVALID_REQUEST`, 성공 응답만 멱등 저장
  - D70: escalate body와 동작. operator는 표대로 PR_OPENED에서만 중단할 수 있다. 결합 표가 요구하는 알림 intent를 위해 outbox `enqueue`(기록 부분)를 W26 시그니처로 먼저 만들었다
- 증거: 커밋은 이 보고를 포함한 W06 커밋. DB는 git 제외 경로 `runs/linemedic.db`에 있다
- 남은 일·위험:
  - W26 전에는 PENDING 알림이 발송되지 않고 쌓인다. route catalog 검증·발송·재시도는 W26
  - agent token 등록부는 메모리다. 프로세스를 다시 시작하면 모두 무효가 된다(진행 중 attempt 자동 재개 없음과 같은 방향)
  - operator 중단은 허용 표대로 PR_OPENED에서만 된다. 시작 전 취소는 W25의 work cancel이 맡는다
- 다음 카드: W05 (2부)

### W05 (1부) 완료 보고 (2026-09-27T05:07Z)

- 상태: UNIT_TESTED (1부 목표 도달). docker 마커 시험과 `make verify-negative`도 로컬 Docker에서 통과했다. 2부(DB 저장·verifier 전용 incident 전이)는 W06 뒤에 한다
- 변경 파일:
  - `linemedic/contracts/defect-summary-v1.toml`: spec 08 §4 YAML과 같은 필드·값(D60). holdout 기대값은 두지 않고 `fixture_ref`로 `linemedic/eval/`에서 읽는다
  - `linemedic/control_plane/verifier.py`: 엄격한 계약 모델(`require_*`는 `true`만), `resolve_cases`, assertion 5종 + 상태 코드 검사(JSON 정수만, bool·음수·문자열 숫자·실수·중복 key 거부), `FixtureGuard`, `VerificationRun`/`verify`(t=0·10·20·30 표본, t=60 이전 PASS 없음, 반증 시 조기 FAIL), `ProberHttp`(신뢰 prober 컨테이너에서 stdlib urllib 고정 코드 실행), 결과에 spec 08 §8 필드 전부
  - `linemedic/control_plane/observer.py`: `ContainerObserver`(t0부터 `docker logs --follow --since`, 끊기면 gap, poll마다 host inspect로 container·image ID 비교, `RecurrenceSignature` 재발 계수)
  - `linemedic/integrations/docker.py`: `DockerPort`, `CliDocker`(고정 argv·timeout, 정확한 이름만 삭제), `FakeDocker`·`FakeLogStream`
  - `linemedic/factory_sim/negative/`: `wrong_200_defects.py`(검사자 없는 record가 있으면 200 + `total_defects: 0`), `s1b.Dockerfile`(MES 이미지 위에 그 파일 하나만 덮음), `harness.py`(`run_verify_negative`: S1b 빌드 → run 전용 internal network에 S1b MES·prober 기동 → 관찰 시작 → verifier → 결과 JSON 저장 → 정확한 이름만 정리, origin `human_injected_negative`)
  - `linemedic/cli.py`(`verify-negative`, FAIL/content_mismatch일 때만 종료 코드 0), `Makefile`(`verify-negative`), `linemedic/common/config.py`(계약 로더용 공개 `read_toml`·`validate_model`)
  - `linemedic/factory_sim/scenarios.py`: MES 격리 옵션을 `mes_container_options`로 분리해 S1·S1b가 같이 쓴다. 데이터 mount 경로를 절대 경로로 바꿨다(아래 판단)
  - DECISIONS.md(D67), ADR.md(D67 행, 주제별 보기에 빠져 있던 D65·D66·D67 추가)
  - 테스트: `linemedic/tests/unit/test_verifier.py`(74개), `linemedic/tests/unit/test_mes_seed.py`(상대 `RUNS_DIR` mount 회귀 1개), `linemedic/tests/integration/test_verifier_docker.py`(docker 2개)
- 실행 (로컬 개발 Mac — 데모 호스트 아님):
  - `make test` → 154 passed, 4 deselected / `make lint` → PASS
  - `make test-docker` → 3 passed (실제 Docker 28.1.1)
  - `make verify-negative RUN_ID=r-20260927-050621-a8c3` → 종료 코드 0. `VER-588F634C683A`, FAIL/content_mismatch, samples 1/4, observation_complete=false, resolved_written=false, 경과 0.774초. 실패 assertion: missing-inspector와 variant-held-out의 `exact_total_defects`·`exact_by_inspector_mapping`·`sum_groups_equals_total`(normal-regression은 통과). S1b image `sha256:88793414cb41d118545011a18bfb53da9520989e0d2a09232e47fa705fd7c4f1`, MES image `sha256:425755201561179ca1cf1ee1eccf03ef2559a8d556a9ce0b36b4a32968d5bce0`, prober image `sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f`. 실행 뒤 이 run ID의 컨테이너·network 0개
  - 판정 테스트 변이 확인: PASS 1초 앞당김, bool·실수 허용, 스트림 끊김 무시를 각각 넣으면 테스트가 실패했다(4·2·1개). 확인 뒤 원래 코드로 되돌렸다
- 테스트 ID별 결과:
  - T-VERIFY-01 PASS: t=0~59 판정 없음, t=60에 PASS·observation_complete=true, 표본 호출 시각 0·10·20·30초 × 3 case
  - T-VERIFY-02 PASS: total 0 / 다른 lot_id / 추가 key(`status: resolved`) / `"7"` / `true` / `-1` / `7.0` / 검사자 수 `true` / key 누락 / 미지정 누락 / 그룹 합 초과 / 중복 key / JSON 아님 / 배열 / holdout total 0 → 모두 FAIL/content_mismatch, 표본 1, observation_complete=false
  - T-VERIFY-04 PASS: 표본 4회 통과 후 t=45 같은 signature 로그 → FAIL/error_recurred. 줄 번호·로트 ID만 다른 줄도 재발, 다른 오류·경로·비JSON 줄은 재발 아님
  - T-VERIFY-05 PASS: t=25 image ID 변경 → INCONCLUSIVE/identity_changed. container 교체·사라짐도 INCONCLUSIVE. fixture 변경·삭제 → INCONCLUSIVE/fixture_changed. 대상이 바뀐 step의 틀린 응답은 반증으로 쓰지 않음
  - core observer PASS: t=40 로그 스트림 종료 → INCONCLUSIVE/observer_gap(재발 0이어도 PASS 아님). 표본 timeout·연결 실패 → INCONCLUSIVE/sample_unanswered
  - docker PASS: 실제 S1b → FAIL/content_mismatch, 실제 버그 base의 118 요청 KeyError 로그 → 재발 1회
- 판단:
  - D67: reason 코드와 판정 순서를 정했다. 기대와 다른 HTTP 상태 코드는 `business_error`로 `content_mismatch`와 구분한다. verifier는 host port 없이 같은 internal network의 신뢰 prober 컨테이너로 원래 경로를 호출한다. S1b는 MES 태그 위에 빌드한다(BuildKit이 `FROM <image ID>`를 받지 않음을 확인했다)
  - W04 버그 수정: 실제 `make verify-negative` 첫 실행에서 기본 `RUNS_DIR=runs`(상대 경로)가 `--volume`에서 named volume 이름으로 해석되어 `docker run`이 실패했다. W04의 `scenario-s1`도 같은 문제가 있었다(docker 테스트는 절대 경로 tmp를 써서 놓쳤다). `mes_container_options`에서 절대 경로로 바꾸고 회귀 테스트를 더했다
  - 1부 결과는 DB 없이 `runs/<run_id>/verifications/`에 저장하고 `resolved_written`은 항상 false다
- 증거: 커밋은 이 보고를 포함한 W05 커밋. 결과 JSON은 git에서 제외된 `runs/` 아래에 있다
- 남은 일·위험:
  - 2부: `persist_result`, verifier 전용 `VERIFYING → RESOLVED/ESCALATED` 전이, `linemedic/tests/helpers/demo_states.py`, `make verify-negative`의 DB 기록(W06 뒤)
  - `docker logs --since`는 host 시각을 쓴다. Docker VM 시계가 어긋나면 t0 경계의 줄을 놓치거나 더 읽을 수 있다. heartbeat·cursor 연속성은 H03 hardening 범위다
  - observer는 컨테이너 stdout만 읽는다(MES JSON 로그는 stdout). S1b image ID는 빌드마다 바뀌므로 결과에 쓰인 ID를 기록한다. prober image는 태그로 부르고 ID를 결과에 남긴다. 데모 호스트(G1)에서는 digest 고정을 검토한다
- 다음 카드: W06

### W04 완료 보고 (2026-09-27T04:19Z)

- 상태: UNIT_TESTED (카드 목표 도달). docker 마커 시험도 로컬 Docker에서 통과했다
- 변경 파일:
  - `l3-mes-api-seed/`: FastAPI MES(`app/main.py`), 버그 base 집계(`app/defects.py`, `row["inspector_id"]` 직접 접근), 로트 로더(`app/data.py`, 로트 ID 형식 검사로 경로 이동 차단), JSON Lines 로그(`app/logging_json.py`), 공개 로트 118·101, 보호 회귀(`tests/regression/`), `tests/repro/.gitkeep`, 업무 규칙만 적은 README. 정답 코드·"미지정" 상수·holdout·시나리오 이름 없음
  - `linemedic/eval/holdout-defects-v1.json`(L3-HOLDOUT-201 입력·기대값), `linemedic/factory_sim/fixtures/`(빈 로트, 공개 로트는 시드 경로 참조)
  - `linemedic/runner/mes.Dockerfile`: 신뢰 레시피(의존성 13개 버전 고정·`--no-deps`·`pip check`, `app/`만 복사, 비루트, uvicorn access log 끔)
  - `linemedic/factory_sim/scenarios.py`: `inject_s1`(internal network·비루트·read-only·capability 제거·자원 상한·데이터 read-only mount, 컨테이너 안에서 로트 118 요청 3회·101 요청 1회), `read_mes_logs`, `stop_s1`(정확한 이름만 정리)
  - `linemedic/scripts/seed_demo_repo.py`: 작성자·시각·메시지 고정, 사용자 git 설정·hook 미사용, 비어 있지 않은 출력 디렉터리 거부. push 없음
  - `linemedic/cli.py`(`scenario-s1`), `Makefile`(`mes-image`, `scenario-s1`), DECISIONS.md·ADR.md(D66)
  - 테스트: `linemedic/tests/unit/test_mes_seed.py`(16개), `linemedic/tests/integration/test_mes_container.py`(docker)
- 실행 (로컬 개발 Mac — 데모 호스트 아님):
  - `make test` → 79 passed, 2 deselected / `make lint` → PASS
  - `make test-docker` → 1 passed (실제 Docker 28.1.1)
  - `make mes-image` → `mes_image_id=sha256:425755201561179ca1cf1ee1eccf03ef2559a8d556a9ce0b36b4a32968d5bce0`, `base_repo_digests=["python@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f"]`
  - `python -m linemedic.scripts.seed_demo_repo --output runs/seed-repo` → commit `19045b62f292dedff24529cab505e6d86a91ed8c`, tree `e6718ce7deb861efd2d4916cbd27ef3078c621e3` (단위 테스트에서 두 번 만들어 같은 값 확인)
  - `python -m linemedic.cli scenario-s1 --run-id bad-id` → 종료 코드 1(run_id 형식 거부). 시험 뒤 남은 linemedic 컨테이너·network 0개
- 테스트: fixture 무결성(7·3·5건, 키 없는 record 2·0·3, defect_id 고유, "key 없음" 문자열 없음), holdout 기대값과 업무 규칙 일치, 118 → 500 + D57(+`top_frame_line`) 필드 로그(`KeyError`·`inspector_id`·`app.defects:summarize`, 비밀·절대 경로 없음), 101 정확 집계·404·400·healthz, 빈 로트 API, 보호 회귀가 버그 base에서 통과(subprocess pytest), 시드에 정답·holdout·시나리오 이름 없음, 레시피가 `app/`만 복사·버전 고정, 시드 이력 결정성, 비어 있지 않은 출력 거부, `inject_s1` 격리 옵션·요청 순서·holdout 미포함, 잘못된 run_id·이미지 없음 거부, `stop_s1` 정확한 이름
- 판단:
  - D66: 줄 번호를 `top_frame_line`으로 분리하고 로트 파일 형식을 `{lot_id, records}`로 정했다
  - `inject_s1`은 internal network라 호스트 port를 열지 않고, 요청을 컨테이너 안에서 자기 자신에게 보낸다. W05·W12의 verifier가 MES에 닿는 경로는 그 카드에서 정해야 한다
  - `make mes-image`는 base image를 먼저 pull한다. Docker Desktop BuildKit이 빌드 중 받은 base를 로컬 목록에 남기지 않아 digest를 조회할 수 없었기 때문이다
  - starlette TestClient가 httpx 사용 경고(StarletteDeprecationWarning)를 낸다. 동작에는 영향이 없어 그대로 두었다
- 증거: 커밋은 이 보고를 포함한 W04 커밋. 이미지 ID·digest·시드 SHA는 위 실행 결과 그대로다
- 남은 일·위험: W03의 시드 push는 G2 이후 사람 허락으로 진행하고 그때 원격 main SHA를 `BASELINE_COMMIT`으로 기록한다. 데모 호스트(G1)에서 이미지를 다시 빌드해 ID를 기록해야 한다
- 다음 카드: W05 (1부)

### W03 중단 보고 — live 부분 G2·G10 대기 (2026-09-27T04:02Z)

- 상태: UNIT_TESTED (live: BLOCKED_ON_HUMAN G2·G10). 점검·시험 스크립트와 단위 테스트까지 끝냈고, GitHub에는 읽지도 쓰지도 않았다
- 변경 파일:
  - `linemedic/scripts/github_setup_check.py`: 읽기 전용 점검(repo ID·이름, squash만 허용, 봇 identity·리뷰어와 다름, 봇 관리자 권한 없음·push 가능·App 설치 범위, `baseline/*` 보호). 보호 규칙은 기존 branch protection(GraphQL)과 ruleset(REST)을 모두 확인하고, 실제 적용 설정을 `observed`로 남긴다
  - `linemedic/scripts/github_protection_probe.py`: 쓰기 시험. 기본은 계획만 출력(PLANNED), `--confirm-write`일 때만 실행. 봇의 baseline 직접 쓰기 거절, 리뷰 없는 squash 머지 거절을 확인하고, 리뷰어 승인 가능 여부는 MANUAL로 남긴다. 만든 브랜치·PR은 삭제하지 않고 `probe` 라벨
  - `linemedic/scripts/doctor.py`: `github` 항목(필수) — credential·repo 설정 확인 후 봇 credential로 repo ID 일치 확인
  - `linemedic/tests/unit/test_github_setup.py`(17개), `linemedic/tests/unit/test_common.py`(doctor 전체 OK 테스트에 가짜 GitHub 조회 주입)
- 실행 (로컬 개발 Mac, mock — live 아님):
  - `make test` → 63 passed, 1 deselected / `make lint` → PASS
  - `github_setup_check --env-file /dev/null` → 종료 코드 2 / `github_protection_probe --env-file /dev/null` → 종료 코드 2
  - `python -m linemedic.cli doctor` → `github` NOT_CONFIGURED, 비밀 값 출력 없음
- 판단:
  - 보호 조건: 필수 승인 1 이상, stale approval 폐기, 관리자 우회 불가, PR·force push 우회 허용 대상 0, force push 금지. `requireLastPushApproval`은 판정에 넣지 않고 관찰값으로만 기록했다(카드가 "최신 변경 승인(stale approval 폐기)"로 적어 둘을 같은 조건으로 본다)
  - 거절 판정: 직접 쓰기는 HTTP 403·405·409·422를 거절로 본다. 리뷰 없는 머지는 405만 거절로 보고, 그 밖의 4xx는 원인이 보호인지 권한인지 알 수 없어 INCONCLUSIVE로 둔다
  - 봇 권한: 사용자 토큰은 "다른 repo 접근 여부"를 API 응답으로 확인할 수 없어 PASS 설명에 그 한계를 적는다
  - GitHub API 버전 헤더는 N11 전이라 기본으로 보내지 않고 `--api-version` 선택 옵션으로만 둔다(D46)
- 증거: 커밋은 이 보고를 포함한 W03 커밋. `evidence/github-setup-check.json`·`evidence/github-protection-probe.json`은 실제 실행 전이라 없다
- 남은 일·재개 조건: G2가 열리면 설정 점검 실행 → 사용자 허락 후 보호 쓰기 시험 → W04 이후 `seed_demo_repo.py`의 push 부분 구현·실행과 `BASELINE_COMMIT` 기록
- 다음 카드: W04

### W02 중단 보고 — live 부분 G3·G4·G5 대기 (2026-09-27T03:23Z)

- 상태: UNIT_TESTED (live: BLOCKED_ON_HUMAN G3·G4·G5). 스크립트 작성과 단위 테스트까지 끝냈고, 실제 스파이크는 하나도 실행하지 않았다
- 변경 파일: `linemedic/scripts/spikes/n01_model_tool_call.py`(가짜 `get_incident` 도구로 tool call → 결과 재입력 → 구조화 제안 확인, 모델 ID·request ID·지연·token 기록, 키 미출력), `linemedic/scripts/spikes/README.md`(N01 실행법, N02~N05·N08~N10 수동 체크리스트, evidence 양식), `linemedic/tests/unit/test_spike_n01.py`, `linemedic/tests/live/test_model_toolcall.py`(live_model)
- 실행 (로컬 개발 Mac, mock — live 아님):
  - `make test` → 46 passed, 1 deselected
  - `make lint` → PASS
  - `make test-live` → 1 skipped (`NOT_CONFIGURED (G3)`, 필수 env 없음). 통과로 세지 않는다
  - `python -m linemedic.scripts.spikes.n01_model_tool_call --env-file /dev/null` → 종료 코드 2, `NOT_CONFIGURED`
- 테스트: `test_spike_n01.py` 8개 — 키 없을 때 NOT_CONFIGURED·비밀 미출력, 정상 왕복 PASS(요청 형식·tool 메시지·request ID·usage 기록 확인), 도구 미호출 FAIL, OpenAI 비호환 응답 INCONCLUSIVE, 401 FAIL(키 미노출), 429 INCONCLUSIVE, 제안이 JSON이 아니면 FAIL, 금지 필드(confidence) FAIL
- 판단: 스파이크 판정의 기준은 카드의 통과 증거(tool → 결과 재입력 → 제안 1회)를 따랐다. 최종 제안은 항상 허용되는 `escalate` 형식을 요청해 도구 왕복과 구조화 출력만 시험한다. N02의 "fake `/tools`에 제안 제출"은 W06~W09 이전이면 NOT_RUN으로 두도록 README에 적었다
- 증거: 커밋은 이 보고를 포함한 W02 커밋. `evidence/spikes/`는 실제 실행 전이라 없다
- 남은 일·재개 조건: G3 키가 들어오면 N01 실행 → `evidence/spikes/N01-model-tool-call.{json,md}` 기록. 이어서 N02(runtime) → G4 결정 → G5 이후 N03·N04·N09·N10, N05 secret inventory
- 다음 카드: W03 게이트 없는 부분

### W01 중단 보고 — G6 대기 (2026-09-27T03:15Z)

- 상태: BLOCKED (BLOCKED_ON_HUMAN: G6). 게이트 없이 할 수 있는 부분(기록 양식)은 끝냈다
- 변경 파일: `evidence/contest-conditions.md`(R1~R5 상태 표, 주최 측 답변 기록 표, 공식 페이지 관찰 기록, 답변 후 할 일), STATUS.md(W01 행·대회 조건 근거·다음 작업)
- 실행: 코드 변경 없음 → `make test` NOT_RUN(해당 없음). 문의 발송·신청서 제출은 하지 않았다(사람의 일)
- 상태 판단: R1·R2·R4·R5는 UNCONFIRMED, R3는 기존 STATUS와 같게 PROVISIONAL로 두었다(카드는 "모두 UNCONFIRMED"라고 적지만 STATUS가 이미 제공 자료 기준 PROVISIONAL로 기록해 둔 값과 맞췄다). 공식 페이지 관찰은 주최 측 답변이 아니므로 상태를 바꾸지 않았다
- 증거: `evidence/contest-conditions.md`, 커밋은 이 보고를 포함한 W01 커밋
- 남은 일·재개 조건: 사람이 spec 12 §2 문안을 주최 측에 보내고 답변 원문을 전달하면 §2에 옮기고 R 상태·영향 카드(W14·W21·Guardrails)를 갱신한다
- 다음 카드: W02 게이트 없는 부분

### B00 완료 보고 (2026-09-27T02:27Z)

- 상태: UNIT_TESTED (카드 목표 도달)
- 변경 파일: `pyproject.toml`(허용 의존성·pytest 마커·ruff), `Makefile`(setup/lock/test/test-docker/test-live/lint/fmt/doctor/host-manifest), `.gitignore`(패키징 산출물 추가), `.env.example`(spec 11 §2 이름만, 비밀 표시), `config/linemedic.toml`(docs/07 기본값, 미확정 키 생략), `requirements.lock`, `linemedic/common/{clock,ids,canonical_json,config}.py`, `linemedic/cli.py`, `linemedic/scripts/{host_manifest,doctor}.py`, `linemedic/tests/{conftest.py,unit/test_common.py}`, `evidence/README.md`, DECISIONS.md·ADR.md(D65)
- 실행 (로컬 개발 Mac, fake/unit — live 아님):
  - `make test` → 38 passed / `make lint` → PASS (ruff check, format check 14 files)
  - `make test-docker` → NOT_RUN (docker 마커 테스트 없음) / `make test-live` → 실행 안 함
  - `python -m linemedic.cli doctor` → 종료 코드 1 (`env` NOT_CONFIGURED, 필수 변수 18개 미설정 이름만 표시, 비밀 값 출력 없음). `make doctor`는 이를 make 오류로 보고한다
  - 새 clone(커밋 cc2766b)에서 `make setup` exit 0 → `make test` 38 passed → `make lint` PASS
- 테스트: `linemedic/tests/unit/test_common.py` — 중복 key·128 KiB 상한·NaN·비UTF-8 거부, canonical 동일성·sha256 안정성, run_id·엔티티 ID 형식, FakeClock 10초 advance, config_hash(비밀 env 무관·deadline 변경 시 변경·호스트 식별 env 무관), 모르는 키·잘못된 타입·TOML 날짜/시각·크기 상한 거부, 생략 키 None, 잘못된 env 값 거부, Secrets repr 비노출, doctor NOT_CONFIGURED·종료 코드·비밀 미출력, host manifest null 처리
- 환경: 개발 Mac(arm64, kernel 25.6.0), Python 3.12.2, SQLite 3.46.0(FTS5), git 2.55.0(Homebrew), Docker 28.1.1. **데모 호스트 manifest가 아니다** — W00에서 확정 호스트로 다시 만든다
- 결정·해석: D65(config hash 범위). ruff 대상을 Python 파일로 한정했다(ruff 0.16이 Markdown도 포맷 대상으로 잡아 spec/ 수정 위험). doctor 필수 env는 config 기본값이 없는 18개로 정했다
- 증거: 커밋 `cc2766b6c8fc2ca221893a10fe2c2602745ca4aa`(구현). 후속 커밋에서 `make host-manifest`가 명령줄을 출력하지 않게 고침(W00의 `make host-manifest > evidence/host-manifest.json`이 순수 JSON이 되도록, 출력 JSON 파싱 확인) 및 이 보고 기록
- 남은 일·위험: 사용자 허락으로 fork `minjcho/medicAgent`의 `b00-bootstrap` 브랜치에 push하고 팀 저장소에 PR [#33](https://github.com/lineMedic/medicAgent/pull/33)을 열었다(2026-09-27T03:11Z, 이슈 #1 연결). 머지는 팀장 리뷰 후 결정한다. upstream `lineMedic/medicAgent` 쓰기 권한은 없다
- 다음 카드: W01 게이트 없는 부분 (W00은 G1 대기)

### D64 승인 허용 후 게시 재개 — 실행 제한 지속 (2026-09-26T18:05:17Z)

- 범위: 사용자 요청에 따라 준비된 문서 변경의 커밋·push를 재개했다. 대상은 `lineMedic/medicAgent`다.
- 승인된 실행: `git ls-remote --symref https://github.com/lineMedic/medicAgent.git` → FAIL(`Could not resolve host: github.com`), `git remote add origin https://github.com/lineMedic/medicAgent.git` → FAIL(`.git/config: Operation not permitted`), `git add -- ADR.md DECISIONS.md STATUS.md` → FAIL(`.git/index.lock: Operation not permitted`). 모두 `require_escalated`로 실행했으나 기존 오류가 지속됐다. 새 커밋·push는 NOT_RUN이다.
- 읽기 전용 확인: 실행 사용자와 `.git`·config·index 소유자는 `jgoneit`이며 소유자 쓰기 비트가 있다. 기본 셸에는 `CODEX_SANDBOX=seatbelt`, `CODEX_SANDBOX_NETWORK_DISABLED=1`이 적용돼 있다. 승인된 실행에서 제한이 지속되는 정확한 원인은 미확인이다.
- 변경·검증: 이번 재개에서는 STATUS.md에 실행 결과만 추가했다. DECISIONS.md·ADR.md의 준비된 변경과 기존 최초 커밋을 보존했다. `git diff --check` → PASS(로컬). 제품 테스트 → NOT_RUN(제품 코드·Makefile 생성 전).
- 남은 일: 실제 Git 메타데이터 쓰기·GitHub 네트워크 접근이 가능한 실행 환경에서 스테이징·커밋·origin 등록·원격 확인·push·SHA 비교를 재개한다. 현재 origin은 없으며 제품 카드·게이트 상태와 다음 카드 B00은 유지한다.

### D64 사용자 지정 저장소로 커밋·push 재개 (2026-09-26T18:01:30Z)

- 범위: 사용자 지정 `https://github.com/lineMedic/medicAgent`에 현재 프로젝트를 커밋·push. 이전 `lineMedic/lineMedic` 생성 목표는 D64로 대체한다.
- 원격 확인: GitHub 커넥터 저장소 조회 → PASS(live). 조직 `lineMedic`, 공개 저장소 `medicAgent`, 기본 브랜치 `main`, 현재 연결 계정의 `push`·`admin` 권한 확인. 원격 커밋·트리와 로컬 SHA 일치는 미확인이다.
- 로컬 상태: 기존 최초 커밋은 `4957f61b5b78096912449d4a838df7ff70efb526`. 시작 시 변경은 STATUS.md 1개였으며 기존 origin은 없었다.
- 실행: `gh repo view lineMedic/medicAgent` → FAIL(API 연결 실패), `git ls-remote --symref https://github.com/lineMedic/medicAgent.git` → FAIL(`Could not resolve host: github.com`), `git remote add origin https://github.com/lineMedic/medicAgent.git` → FAIL(`.git/config: Operation not permitted`). 원격 등록은 미완료다.
- 커밋·검증: `git add -- ADR.md DECISIONS.md STATUS.md` → FAIL(`.git/index.lock: Operation not permitted`), 새 커밋·push → NOT_RUN(스테이징 차단). `git diff --check` → PASS(로컬 문서 공백 검사). 기존 최초 커밋과 문서 변경은 보존했다.
- 변경 파일: DECISIONS.md·ADR.md에 D64와 대체 관계, STATUS.md에 실제 조회 결과와 재개 조건 기록. 제품 테스트는 NOT_RUN(문서만 변경, 제품 코드·Makefile 생성 전).
- 남은 일·재개 조건: 이 작업 폴더의 Git 메타데이터 쓰기와 셸의 GitHub 네트워크 접근이 가능한 환경에서 문서 변경을 커밋하고, origin 등록·원격 이력 확인 후 main을 push하여 SHA를 비교한다. G2·G10과 제품 카드 상태는 변경하지 않으며 다음 제품 카드는 B00 유지.

### GitHub 조직 생성 재요청 — 접근 차단 (2026-09-26T17:52:33Z)

- 범위: 사용자 요청의 GitHub 조직 `lineMedic` 생성. 제품 카드 구현과 별개다.
- 실행: GitHub 조직 설정 페이지 열기 → FAIL(브라우저 보안 정책: 사용자가 해당 사이트 접근을 거부했다는 응답). 조직 생성은 미완료이며 다른 브라우저나 간접 경로로 우회하지 않음.
- 변경 파일: STATUS.md에 이번 차단과 재개 조건만 기록. 제품 테스트는 NOT_RUN(코드 변경 없음).
- 남은 일·재개 조건: GitHub 브라우저 접근을 허용한 뒤 조직 생성 재개, 또는 사용자가 직접 조직 생성 후 결과 확인. G2·G10과 제품 카드 상태는 변경하지 않으며 다음 제품 카드는 B00 유지.

### GitHub 게시 재개·로컬 Git 초기화 (2026-09-26T17:49:19Z)

- 범위: 사용자 요청에 따라 권한 변경 후 게시 작업 재개. 작업 루트가 `lineMedic/`로 변경된 것을 확인했으며 GitHub 생성 목표는 조직 `lineMedic`과 공개 저장소 `lineMedic/lineMedic`이다.
- 실행: `git init -b main` → PASS, `gh auth status`와 `gh api user` → PASS(`jgoneit`). 이전 시도의 Git 초기화·CLI 인증 차단은 해소됨.
- 게시 전 검사: `python3 -B -` → PASS(로컬). 문서 82개와 .gitignore의 이전 이름 잔여 0건, 상대 링크 대상 696건의 누락 0건(앵커 검증 제외), 비밀 키·토큰 형식 검사 후보 0건. 제품 테스트는 NOT_RUN(제품 코드·Makefile 생성 전).
- 커밋 범위: 현재 프로젝트 문서와 .gitignore 전체. 삭제된 진행 도구는 포함하지 않으며 `main`의 최초 커밋으로 기록한다. Git 작성자는 인증된 계정의 공개 noreply 주소를 저장소 로컬 설정으로 사용한다.
- 외부 생성·push: 미완료. GitHub 조직 설정 페이지 접근은 저장된 사용자 차단 설정 때문에 브라우저 자동 권한 검토에서 다시 거부됨. 차단 해제 후 조직·공개 저장소를 생성하고 로컬 `main`을 push하여 원격 SHA와 비교한다. 사용자가 조직을 직접 생성한 경우에도 해당 조직의 권한 확인부터 재개할 수 있다.
- 제품 진행: 카드·게이트·체크포인트는 변경하지 않음. 다음 제품 카드 B00 유지.

### D63 저장소 이름 통일·GitHub 게시 준비 (2026-09-26T17:44:47Z)

- 범위: 사용자 지정 이름 `lineMedic`으로 문서 표기 변경 완료. 조직 `lineMedic`과 공개 저장소 `lineMedic/lineMedic` 생성·push는 접근·인증 설정 후 재개할 미완료 작업이다.
- 변경 파일: README.md·CLAUDE.md·ARCHITECTURE.md·docs/02·12(저장소 이름·루트 경로), DECISIONS.md·ADR.md(D63과 이름 변경 근거), STATUS.md(실행 결과). Git 게시 준비 과정에서 .gitignore에 로컬 환경·자격 증명·개발 산출물 제외 규칙 추가.
- 실행: `python3 -B -` 일회성 문서 검사 → PASS(로컬). 이전 저장소 이름 8곳 변경 후 잔여 0건, 상대 링크 대상 696건의 누락 0건(앵커 검증 제외), spec 원문 26개와 기존 진행표 보존 확인.
- 제품 테스트: `make test` → NOT_RUN(제품 코드·Makefile 생성 전). 제품 카드·게이트·체크포인트 상태는 올리지 않음.
- 게시 시도: `git init -b main` → FAIL(`.git: Operation not permitted`). `gh auth status` → FAIL(인증 토큰 invalid 응답). GitHub 조직 설정 페이지 열기 → 브라우저 권한 검토에서 거부(사용자가 접근을 허용하지 않았다는 응답). 조직·저장소 생성, 커밋, push는 실행하지 못함.
- 재개 조건: 이 작업 폴더의 Git 초기화 권한, GitHub CLI 로그인과 조직 생성 페이지 접근 허용. 실제 로컬 작업 폴더 이름은 변경하지 않음. 제품의 G2·G10 완료를 뜻하지 않으며 다음 제품 카드는 B00 유지.

### D62 개발 진행 관리 단순화 (2026-09-26T17:38:45Z)

- 범위: 사용자 요청에 따른 문서·개발 도구 정리 완료. 제품 카드 상태는 올리지 않음.
- 삭제: 진행 관리용 Python 파일 2개, TOML 파일 2개, 별도 보고 파일 1개와 실행 캐시 2개.
- 변경 파일: AGENTS.md·CLAUDE.md·STATUS.md(직접 기록·완료·차단·재개 절차), README.md·ARCHITECTURE.md·docs/01·02(문서 지도), docs/08~12·tasks/README·B00·W00·W01·W29(명령·보고 경로·증거 점검), DECISIONS.md·ADR.md(D62와 대체 이력).
- 실행: `python3 -B -` 일회성 문서 정합성 검사 → PASS(로컬, 외부 연동 없음). 상대 링크 대상 696건의 누락 0건(앵커 검증 제외), 현행 안내의 삭제된 명령·파일 참조 0건, 진행 도구 디렉터리 삭제 확인.
- 보존 확인: spec 원문 26개, 작업표 33행, 사람 게이트 12행, 체크포인트 6행, 대회 조건 5행과 현재 작업 표가 변경 전과 일치. B00은 NOT_CHECKED 유지.
- 제품 테스트: `make test` → NOT_RUN(제품 코드·Makefile 생성 전). 이번 검사는 제품 unit·E2E 검증이 아님.
- 커밋: `git status --short` → FAIL(Git 저장소 초기화 전). 커밋 없음.
- 남은 일·게이트: 이번 삭제 범위의 미완료 없음, 추가 게이트 없음. 다음 제품 카드: B00; 구현 착수 시 docs/08 §2의 사람 게이트 요청부터 진행.
