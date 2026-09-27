# STATUS — LineMedic v4 구현 진행표

> **개발 진행 상태와 완료 보고의 유일한 원본 — 직접 편집한다.** 착수·완료·차단·재개 때 아래 표와 완료 보고를 함께 갱신한다([D62](DECISIONS.md)). 작업 범위·선행·게이트·수용 기준은 [tasks/](tasks/README.md) 카드에서 확인한다.
> 상태 enum과 증거 규칙은 [docs/11](docs/11-definition-of-done.md)에 있다. 시각은 UTC로 적는다(예: `2026-09-27T05:10Z`). 상태를 올리기 전에 선행·해당 실행의 사람 승인·원본 증거를 직접 확인한다. 비밀값·수신 주소를 기록하지 않는다.
> 초기값은 원본 [spec 10 §2](spec/docs/10-delivery-plan.md)과 같이 전부 `NOT_CHECKED`다. 이 문서 패키지를 작성한 것만으로는 어떤 W도 완료되지 않았다.

## 현재 작업

| 카드 | 시작 시각 | 진행 메모 |
|---|---|---|
| (없음) | | |

## 다음 작업

[AGENTS.md §2](AGENTS.md)의 선택 조건에 따른 다음 카드: **W11** ([tasks/W11-bot-pr-reconcile.md](tasks/W11-bot-pr-reconcile.md), 봇 PR 생성·결과 불명 기록·reconcile CLI — 선행 W10·W24~W26(fake) 충족. FakeGitHub로 UNIT_TESTED까지, 실제 PR은 G2·G10 대기). W00은 G1, W01은 G6, W02 live는 G3·G4·G5, W03·W22·W24·W26 live는 G2·G10, W23 live는 G2 대기다.

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
| 10 | W07 | UNIT_TESTED | UNIT_TESTED | `make test` → 641 passed(W07 테스트 74개 포함, 전체 검증 수정 뒤 665), `make lint` → PASS, `make test-docker` → 4 passed(실제 S1 로그 → 사건 1개 → 조회 도구), `make scenario-s1`·`make detect-once RUN_ID=r-20260927-054424-94f9` → `DEPLOY_OBSERVED` 기록, 사건 `INC-885B28A026C0` NEW(count 3·증거 3·line L3·fp-v1), 로그 5줄 `runs/r-20260927-054424-94f9/logs/mes-api.jsonl`(git 제외), 정리 뒤 컨테이너·network 0개 | | 2026-09-27T06:22Z |
| 11 | W08 | UNIT_TESTED | UNIT_TESTED | `make test` → 706 passed(W08 테스트 41개 포함), `make lint` → PASS, `make test-docker` → 4 passed, `make scenario-s2-lite RUN_ID=r-20260927-072354-84d8` → `INC-AC32E4E6A0AA`(vision-inspection NEW, count 10, 증거 3, 배포 없음), `RECENT_DEPLOY=1 RUN_ID=r-20260927-072410-712e` → `INC-793CE4A26CC2`(이상 10분 전 mes-api 배포 기록), 지표 파일 `runs/<run>/metrics/`(git 제외) | | 2026-09-27T07:25Z |
| 12 | W09 | UNIT_TESTED | UNIT_TESTED | 독립 리뷰 반영 뒤 `make test` → 862 passed(처음 843, W09 테스트 143개 포함), `make lint` → PASS, `make test-docker` → 4 passed, `make api-schema` → `linemedic/contracts/api/*.schema.json` 3개(`--check` 최신), 실제 HTTP(uvicorn 127.0.0.1 + httpx, 임시 DB) 12/12 PASS: create_pr 202 → REJECTED(PROTECTION_UNAVAILABLE)·수정 허용 → escalate 202 → ESCALATED/BLOCKED(UNSUPPORTED_ACTION)·WORK_BLOCKED intent, 변이 25개 모두 테스트 실패로 잡힘, 리뷰 지적 4건 수정(B00·W06·W09 브랜치) | | 2026-09-27T09:17Z |
| 13 | W22 | LIVE_VERIFIED | UNIT_TESTED (live: BLOCKED_ON_HUMAN G2·G10) | `make test` → 963 passed(W22 테스트 101개 포함: 포트 계약·Fake·HttpGitHub MockTransport 78, catalog·설정 18, doctor 5), `make lint` → PASS, `make test-live` → github smoke 2 skipped(NOT_CONFIGURED G2), 변이 21개 모두 테스트 실패로 잡힘. GitHub 호출 없음 | BLOCKED_ON_HUMAN: G2 — 데모 repo·봇 credential·`GITHUB_REPOSITORY(_ID)` / 확인: `make test-live`(github 읽기 계약). G10 + 사용자 허락 — config `github.write_enabled = true`와 `LINEMEDIC_CONFIRM_GITHUB_WRITE=1`로 Issue·댓글 smoke 1회, receipt `evidence/N11-github-smoke.md`, N11로 `github.api_version` 확정 | 2026-09-27T09:33Z |
| 14 | W23 | LIVE_VERIFIED | UNIT_TESTED (live: BLOCKED_ON_HUMAN G2) | `make test` → 1003 passed(W23 테스트 40개: polling 통합 39, 포트 1), `make lint` → PASS, `make test-live` → S5-new 1 skipped(`LINEMEDIC_LIVE_S5` 표시 없음)·github smoke 2 skipped(G2), 변이 30개 모두 테스트 실패로 잡힘. GitHub 호출 없음 | BLOCKED_ON_HUMAN: G2 — 데모 repo·봇 credential·`ISSUE_TRUSTED_AUTHOR_IDS` / 확인: `LINEMEDIC_LIVE_S5=1 make test-live` 중 승인된 작성자가 새 Issue 1개 생성 → `evidence/S5-new-issue-detect.md`(감지·생성 시각) | 2026-09-27T10:04Z |
| 15 | W24 | LIVE_VERIFIED | UNIT_TESTED (live: BLOCKED_ON_HUMAN G2·G10) | `make test` → 1036 passed(W24 테스트 33개), `make lint` → PASS, `make test-live` → S4 2 skipped(쓰기 허락·후보 준비 표시 없음), 변이 27개 모두 테스트 실패로 잡힘(W23 변이 30개도 다시 확인). GitHub 호출 없음 | BLOCKED_ON_HUMAN: G2 — 데모 repo·봇 credential / G10 + 사용자 허락 — `write_enabled = true`와 `LINEMEDIC_CONFIRM_GITHUB_WRITE=1`로 S4-new·existing 1회, 사람이 후보 Issue 2개를 만든 뒤 `LINEMEDIC_LIVE_S4_AMBIGUOUS=1`로 S4-ambiguous 1회 → `evidence/S4-issue-live.md`. issue form을 데모 repo에 복사 | 2026-09-27T10:33Z |
| 16 | W25 | UNIT_TESTED | UNIT_TESTED | `make test` → 1067 passed(W25 테스트 31개: 경합 7, lifecycle 24), `make lint` → PASS, 경합 시험(스레드 2·4·8, 각자 DB 연결): 활성 work 1·`WORK_STARTING` 1·attempt 최대 1, 변이 20개 모두 테스트 실패로 잡힘 | | 2026-09-27T10:53Z |
| 17 | W26 | LIVE_VERIFIED | UNIT_TESTED (live: BLOCKED_ON_HUMAN G2·G10) | `make test` → 1098 passed(W26 테스트 31개: 알림 25, 시작 게이트 6), `make lint` → PASS, `make test-live` → N12 1 skipped(쓰기 허락 표시 없음), 변이 24개 모두 테스트 실패로 잡힘, live 시험 흐름을 FakeGitHub로 한 번 따라 실행. GitHub 호출 없음 | BLOCKED_ON_HUMAN: G2 — 데모 repo·봇 credential / G10 + 사용자 허락 — `write_enabled = true`, `LINEMEDIC_CONFIRM_GITHUB_WRITE=1`, `LINEMEDIC_LIVE_NOTIFY_ISSUE=<open Issue 번호>`로 `make test-live` 1회 → `evidence/N12-notification-route.md`(시작 댓글·S6 차단 댓글·강제 timeout 조정·미전송). SMTP는 G12 선택 시에만 | 2026-09-27T11:22Z |
| 18 | W10 | UNIT_TESTED | UNIT_TESTED | `make test` → 1293 passed(W10 테스트 195개: 정책 98, 판정 54, runner 단계 13, 게이트·브로커 30), `make test-docker` → 11 passed(W10 실제 컨테이너 7개: R0/R1/R2·비재현·회귀·timeout·OOM·N06 격리), `make lint` → PASS, 변이 79개 중 78개가 테스트 실패로 잡힘(1개는 동등 변이), `evidence/N06-runner-isolation.md`(로컬 개발 Mac) | | 2026-09-27T12:25Z |
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

### W10 리뷰 반영 (카드 밖, 2026-09-27T13:00Z)

- 계기: PR #50 리뷰(CHANGES_REQUESTED) — 실제 Docker에서 재현된 운영 안전 문제 2건
- 수정:
  1. Docker 호출 오류로 work가 RUNNING에 굳음: `Runner.run_stage`가 `docker run` 뒤의 `wait`·inspect·로그 읽기·로그 쓰기 오류를 runner 오류(`docker_wait_failed`·`docker_inspect_failed`·`docker_logs_failed`·`log_write_failed`)로 돌려준다. `PatchGate.check`는 그 밖의 예상하지 못한 예외를 `PROTECTION_UNAVAILABLE`(`unexpected_error:<종류>`, 수정 불가)로 닫아, 브로커의 거절 경로(ESCALATED/BLOCKED·차단 보고)를 탄다
  2. 결과 mount가 host 디스크에 무제한 쓰기 가능: 결과 폴더를 0555로 두고 미리 만든 `junit.xml`(0666) 한 파일만 쓰게 했다(새 파일·폴더·symlink 불가). `--ulimit fsize`를 `tmpfs_mib`(64 MiB)로 걸고 inspect에서 확인한다. 단계 뒤 0755로 되돌려 host가 정리할 수 있다(Linux에서 uid 10001 소유 하위 폴더가 생기지 않는다). 실행 뒤 다른 항목이 있으면 `unexpected_result_entries`
- 실행 (로컬 개발 Mac, Docker server 28.1.1):
  - `make test` 상당 → Python 3.12.2 1301 passed, 3.14.4 1300 passed·1 failed(`test_loads_strict_rejects_deeply_nested_json_as_strict_error`, #49에서 수정), `ruff check`·`ruff format --check` PASS
  - `make test-docker` 상당 → 11 passed. 새 probe `test_results_mount_cannot_fill_the_host_disk`: 컨테이너 안에서 결과 폴더에 파일·폴더·symlink 생성 실패, `junit.xml` 쓰기가 정확히 64 MiB에서 EFBIG
  - `LINEMEDIC_RECORD_EVIDENCE=1`로 `evidence/N06-runner-isolation.md`를 다시 기록(probe 7개, inspect `ulimits` fsize 64 MiB)
  - 새 테스트 12개(단위 10, 게이트·브로커 2)는 수정 전 코드에서 실패하는 것을 먼저 확인했다. 리뷰 재현(`wait`가 `No such container`) → proposal PROTECTION_UNAVAILABLE(`r0_docker_wait_failed`), incident ESCALATED, work BLOCKED, 차단 알림 1건, 컨테이너 없음
- 판단: D80 ⑤ 보충, docs/07 §2에 결과 mount 예외와 파일 크기 상한을 적었다. 프로세스가 죽는 경우(예외가 아닌 종료)는 지금처럼 CHECKING으로 남고 재시작 때 `recover_checking`이 다시 검사한다
- 남은 일: 이 브랜치는 #48의 수정 전 커밋(`5a52d5a`) 위에 있다. #48·#49가 병합되면 main을 병합해 다시 확인한다. G1 데모 호스트(Linux)에서 결과 폴더 권한(0555)이 컨테이너 uid 10001의 새 항목 생성을 막는지 N06 재확인 때 본다. W11: PR 본문에 "테스트 PASS는 악성 코드 없음이 아님"과 R1 실패 요약(길이 제한)을 보이기. W13·W14: 게이트 실제 소요 시간 기록

### W10 완료 보고 (2026-09-27T12:25Z)

- 상태: UNIT_TESTED (카드 목표 도달, `make test-docker` 포함). 외부 쓰기 없음
- 변경 파일:
  - `linemedic/policies/broker_policy.toml`(새): 패치 정책의 유일한 원본(config `[patch]`를 옮김, docs/07 §1 갱신)
  - `linemedic/control_plane/broker/patch_policy.py`(새): 정책 로더, diff 해석(git diff의 좁은 부분집합), 경로·파일 형태·상한·보호 경로 규칙, 적용 뒤 tree 재확인·텍스트 확인
  - `linemedic/control_plane/broker/candidate.py`(새): mirror bare 사본 → 임시 index 적용(작업 트리 없음) → tree 재확인 → 서버 commit → repro tree → 세 tree 안전 추출
  - `linemedic/control_plane/broker/runner.py`(새):
    - 고정 image·실행 프로필로 `run_stage`(timeout·로그 상한·정확한 이름 정리), inspect로 실제 적용 값 재확인
    - 결과 mount의 junit을 symlink를 따르지 않고 읽고 DTD를 거부하는 파서, `judge_r0`·`judge_r1`·`judge_r2`
  - `linemedic/control_plane/broker/patch_gate.py`(새): 정책 → 기준 base → candidate → image → R0 → R1 → R2 순서와 거절 코드·수정 가능 여부
  - `linemedic/control_plane/broker/intake.py`: create_pr를 게이트로 교체(트랜잭션 밖 실행, 재확인 뒤 기록, 고칠 수 없는 결과는 즉시 멈춤, W11 전에는 통과해도 멈춤)
  - `linemedic/control_plane/broker/proposals.py`·`tools_api.py`: W10 검사 이름·코드 추가, 에이전트 보기를 check·result·reason으로 축소. `contracts/api/proposal-status.schema.json` 재생성
  - `linemedic/integrations/docker.py`: `wait`·`logs_capped`, `DockerError.returncode`, FakeDocker `run_handler`
  - `linemedic/runner/runner.Dockerfile`·`pytest-protected.ini`(새), `Makefile` `runner-image`, doctor `runner_image`(필수), config `[runner]` uid·gid·tmpfs_mib(격리 항목은 True만)
  - 테스트:
    - `unit/test_patch_policy.py`(98, 저장소 파일의 보이지 않는 문자 검사 포함), `unit/test_repro_judgement.py`(54), `unit/test_runner_stage.py`(13)
    - `integration/test_patch_gate.py`(30), `integration/test_runner_docker.py`(7, docker)
    - 도우미 `helpers/runner.py`, fixture `fixtures/patches/`(시드에서 `git diff`로 만든 7개)·`fixtures/junit/`(실제 pytest 출력 5개)
- 실행 (로컬 개발 Mac):
  - `make test` → 1293 passed, 18 deselected / `make lint` → PASS
  - `make test-docker` → 11 passed(W10 7개). 올바른 수정은 R0 4 passed → R1 KeyError 1 failure → R2 5 passed, 컨테이너는 남지 않는다
  - `LINEMEDIC_RECORD_EVIDENCE=1 make test-docker` → `evidence/N06-runner-isolation.md`: 외부 연결·DNS 실패, repo·root 쓰기 실패, /tmp만 쓰기, uid 10001, Docker socket 없음, inspect 값(network none·read-only·cap ALL 제거·no-new-privileges·512 MiB·swap 없음·CPU 1·PID 64·mount 2개)
  - 변이 확인 79개(각각 넣고 테스트를 돌린 뒤 원래 코드로 되돌렸다). 78개가 테스트 실패로 잡혔다:
    - 정책: glob `*`가 `/`를 넘음, `..`·`.`·`.git` 단계 허용, 제어 문자·symlink·mode 변경·rename(header 포함)·삭제·binary 허용, 파일 수·줄 수 상한(100 경계 양쪽), 보호 경로·기존 테스트 수정·새 테스트 개수·선언 경로, hunk 줄 수·새 파일 범위, index mode, tree 재확인(보호 blob·경로 집합·mode 양쪽), 텍스트 확인
    - candidate: tree 재확인·텍스트 확인 호출 제거, 작성자 고정 제거, repro tree가 candidate, 적용 실패를 환경 문제로, base 확인 제거, tar 필터 제거, base SHA 형식
    - runner: network·read-only·cap-drop·swap·pids·user·readonly mount·noconftest 제거, 제한 시간 무시, timeout 뒤 junit 읽기, 프로필 확인 무시, 같은 이름 정리 제거, symlink 따라감, 일반 파일·DTD·junit 속성 확인 제거, OOM·로그 상한 무시
    - 판정: R1(exit 0·errors·failure 요구·다른 모듈), Docker 125/126/127, R2(회귀 case·새 테스트·skip·종료 코드), R0(skip·종료 코드)
    - 게이트·브로커·보기: R0 실패 수정 허용, base 확인 3종, image 확인, 재검사 디렉터리 재사용, 통과·수정 불가 결과의 수정 허용, 이미 끝난 제안 재기록, SOURCE_CHANGED 매핑, 배포 base 선택 2종, 게이트 없음 통과, 에이전트에게 기록 전체, doctor 태그·ID 불일치
    - 살아남은 1개는 동등 변이다. git env에서 `GIT_CONFIG_GLOBAL=/dev/null`을 빼도 git은 PATH·빈 임시 HOME만 받아 읽을 사용자 설정이 없다(카드가 요구한 명시적 방어로 둔다)
- 수용 기준:
  - T-PATCH-01: `tests/regression/*` 수정, `Dockerfile`·`pyproject.toml`·`.github/*`·`conftest.py`(하위 폴더 포함) 변경 → PATCH_PATH_DENIED(protected_path): PASS
  - T-PATCH-02: `../x`, `/etc/x`, symlink(120000), binary, rename → 거부: PASS
  - T-PATCH-03: 파일 3개 → too_many_files, 101줄 → too_many_lines(100줄은 통과): PASS
  - T-REPRO-01: base에서도 통과하는 새 테스트 → REPRO_NOT_FAILING(passed_on_base), 실제 컨테이너 포함: PASS
  - T-REPRO-02: import 오류(exit 2), 미수집(exit 5), timeout, OOM, skip·xfail만 → 재현 불인정, 실제 컨테이너(import·timeout·OOM) 포함: PASS
  - T-REPRO-03: R1 통과 뒤 candidate의 새 테스트 실패·보호 회귀 실패 → REGRESSION_FAILED, execution(PR 시도) 0: PASS
  - docker: 컨테이너 안 외부 연결 실패, inspect로 network none·read-only·자원 제한 확인, N06 runner 부분 evidence 기록: PASS
- 판단: D80(정책 파일 단일 원본, diff 문법·거부 규칙, 기준 base 3자 일치, 작업 트리 없는 candidate, runner 실행 프로필·inspect 재확인·결과 mount 안전 읽기, 판정 세부, 게이트와 트랜잭션, 에이전트 보기, runner-image·doctor)
- 증거: 커밋은 이 보고를 포함한 W10 커밋. `evidence/N06-runner-isolation.md`
- 작업 중 발견: 보이지 않는 문자(`\u200b` 등)를 escape로 쓰려던 코드·테스트에 글자 그대로 들어간 것을 찾아 escape로 바꿨다. 같은 실수를 막으려고 저장소 코드·설정·문서(spec 제외)에 그런 문자가 없는지 보는 테스트를 더했다
- 남은 일·위험:
  - 통과한 candidate의 봇 PR 생성과 GitHub baseline 브랜치 재조회는 W11이다(지금은 통과해도 PROTECTION_UNAVAILABLE로 멈춘다)
  - trusted mirror(`RUNS_DIR/mirror/l3-mes-api.git`)와 run 기준 commit을 준비하는 절차는 run 준비 카드(W13·W19)에서 붙인다. 없으면 PROTECTION_UNAVAILABLE이다
  - 같은 Python 프로세스의 비신뢰 코드는 junit을 위조할 수 있다. 업무 복구 판단은 배포 뒤 verifier가 따로 한다
  - 검사 디렉터리(`checkouts/<proposal>/<n>`)는 run 정리(W19) 전까지 남는다. 데모 호스트의 N06 확인은 G1 뒤에 다시 한다
- 다음 카드: W11 (fake 부분)

### W26 중단 보고 — live 부분 G2·G10 대기 (2026-09-27T11:22Z)

- 상태: UNIT_TESTED (live: BLOCKED_ON_HUMAN G2·G10). FakeGitHub와 실제 SQLite로 끝냈고, GitHub에는 읽지도 쓰지도 않았다
- 변경 파일:
  - `linemedic/control_plane/notifications/worker.py`(새): `OutboxWorker`
    - `process_pending`(PENDING → SENDING 커밋 → 발송 → 결과 기록), `recover_sending`, `reconcile`
    - 시작 알림이 ACCEPTED가 되면 같은 트랜잭션에서 시작 게이트를 연다
  - `linemedic/control_plane/notifications/github_comment.py`(새): `GitHubCommentAdapter.send`(ACCEPTED·REJECTED(safe_to_retry)·UNKNOWN), `reconcile`(FOUND·CONFIRMED_ABSENT·INCONCLUSIVE·CONFLICT)
  - `linemedic/control_plane/notifications/templates.py`(새): 이벤트 7종 본문, blocker report 렌더러, 알림 marker, 상태 표시 문구
  - `linemedic/control_plane/supervisor.py`:
    - `on_start_notice_accepted`(READY, 늦은 receipt는 감사만)
    - `Supervisor.expire_start_notices`(60초 초과·실패 → BLOCKED `START_NOTICE_UNCONFIRMED`)
    - `start_attempt`의 필수 route 확인과 receipt 두 시각 기록
  - `linemedic/control_plane/ops_api.py`·`app.py`: `GET /ops/notifications`, `POST /ops/notifications/{id}/reconcile`, `AppContext.outbox_worker`
  - `linemedic/cli.py`·`Makefile`: `make notification-reconcile NOTIFICATION_ID=`
  - 테스트: `integration/test_start_gate.py`(6), `integration/test_notifications.py`(25), `live/test_notification_live.py`(N12·S6, live_github)
- 실행 (로컬 개발 Mac, mock — live 아님):
  - `make test` → 1098 passed, 11 deselected / `make lint` → PASS
  - `make test-live` → N12 1개 skipped(쓰기 허락 표시 없음), 나머지 live도 skipped(G2·G3)
  - live 시험 흐름을 FakeGitHub로 한 번 따라 실행했다(실제 GitHub 호출 없음, evidence 파일 만들지 않음):
    - 시작 댓글 → READY → attempt, S6 차단 댓글
    - 강제 timeout → UNKNOWN → 조정 FOUND(댓글 1개), 미전송 FAILED(no_bound_issue)
  - 변이 확인 24개(각각 넣으면 테스트가 실패했고, 확인 뒤 원래 코드로 되돌렸다):
    - worker: UNKNOWN을 재시도로, 안전한 거절도 즉시 FAILED, 재시도 상한 무시, backoff 없음, 재시작 SENDING 복구 없음, receipt 뒤 게이트 안 엶, bound Issue 없어도 보냄, payload의 Issue 번호로 보냄, shadow에서도 보냄, 저장 시각 기록 제거
    - adapter: 조정의 작성자·marker·본문 hash 확인 제거, 연결 전 실패를 UNKNOWN으로
    - 템플릿: 본문 정제 제거, marker 없음, "뜻하지 않는 것" 문구 제거
    - 시작 게이트: 대기 시간 무시, 실패한 시작 알림도 기다림, 필수 route 확인 제거, receipt 두 시각 기록 제거, 늦은 receipt로 부활
    - 운영 API: 목록에 payload 노출
- 수용 기준:
  - T-NOT-01: receipt 전 `start_attempt` 거부(attempt 0), receipt 뒤 READY → attempt, 감사에 receipt 저장 시각 < attempt 시작: PASS. workspace·agent 실행 순서는 그것을 만드는 카드(W28·W13)가 같은 감사에 잇는다(지금은 workspace를 만드는 코드가 없다)
  - T-NOT-02: ACCEPTED 표시 "댓글 등록", 템플릿에 "읽음/배달" 없음: PASS
  - T-NOT-03: 댓글 생성 뒤 timeout → UNKNOWN·재발송 0·조정 FOUND, 재시작 SENDING → UNKNOWN, 중복 enqueue 1건: PASS
  - T-NOT-04: 모델 없이(`MODEL_UNAVAILABLE`) blocker report 완성, 발송(댓글 등록) 또는 미전송(`FAILED(no_bound_issue)`) 기록: PASS
  - T-NOT-05: verifier PASS 뒤 알림 FAILED → incident RESOLVED 유지: PASS
  - T-NOT-06: payload의 수신자·Issue 번호·URL·`@team` → catalog 밖 전송 0, 링크·멘션 무력화, 비밀 마스킹: PASS
  - 60초 초과 → BLOCKED, 뒤에 온 receipt가 work를 되살리지 않음: PASS
  - live 시작 댓글 receipt·S6 차단 알림·N12(접수·강제 timeout·미전송): NOT_RUN (G2·G10)
- 판단: D79(worker 배치, 대상 결정·미전송, shadow, 재시도 범위·backoff, receipt 두 시각, 조정 규칙, 시작 게이트·만료, 템플릿 정제, 상태 표시, 운영 API)
- 증거: 커밋은 이 보고를 포함한 W26 커밋. `evidence/N12-notification-route.md`는 실제 실행 전이라 없다
- 남은 일·위험:
  - G2·G10이 열리면 사람이 전용 repo의 open Issue를 고른다. 사용자 허락 뒤 `LINEMEDIC_CONFIRM_GITHUB_WRITE=1 LINEMEDIC_LIVE_NOTIFY_ISSUE=<번호> make test-live`로 N12를 1회 실행한다(댓글 3개가 남는다). N12가 확인되기 전에는 알림 완료를 주장하지 않는다
  - outbox worker와 시작 알림 만료를 주기적으로 돌리는 루프는 W13(`make start`), 시작 직전 Issue 재조회(EXT)는 W28(지금은 mirror 기준)
  - shadow 모드에서는 승인한 work가 모두 60초 뒤 `START_NOTICE_UNCONFIRMED`로 멈춘다(의도한 동작, D79 대가)
  - SMTP adapter는 G12에서 고를 때만 만든다
- 다음 카드: W10

### W25 완료 보고 (2026-09-27T10:53Z)

- 상태: UNIT_TESTED (카드 목표 도달). 외부 쓰기 없음
- 변경 파일:
  - `linemedic/control_plane/supervisor.py`:
    - 트랜잭션 함수 `approve`(현재 snapshot 승인·`WORK_STARTING` intent), `retry`(새 incident·generation, 중복 거절), `cancel`(시작 전 취소·취소 요청), `recheck_scope`, `ensure_work`(다른 incident 연결 기록)
    - `Supervisor.auto_approve`·`on_scope_changed`·`start_attempt`(시작 게이트, attempt 발급 유일 경로, 슬롯 대기)
  - `linemedic/control_plane/ops_api.py`: `GET /ops/work-items/{id}`, `POST /ops/work-items/{id}/approve`·`/retry`(authorize)·`/cancel`(operate). 상태 변경과 멱등 기록을 한 트랜잭션에
  - `linemedic/cli.py`·`Makefile`: `make approve-work WORK_ID= EXPECTED_VERSION=`, `make retry-work WORK_ID= REASON=`, `make cancel-work WORK_ID=`(D48 HTTP, 공용 `_ops_target`)
  - 테스트: `integration/test_work_claim_race.py`(7), `integration/test_work_lifecycle.py`(24)
- 실행 (로컬 개발 Mac):
  - `make test` → 1067 passed, 10 deselected / `make lint` → PASS
  - 경합: 스레드 2·4·8개가 각자 DB 연결로 같은 Issue에 `ensure_work`+`approve` → 활성 work 1·`WORK_STARTING` 1·나머지 409, 같은 READY work에 `start_attempt` 동시 호출 → attempt 1, 다른 Issue의 READY 둘 → RUNNING 1·대기 1, polling 3개와 로그 route 1개 동시 → 활성 work 1·시작 알림 최대 1
  - 변이 확인 20개(각각 넣으면 테스트가 실패했고, 확인 뒤 원래 코드로 되돌렸다):
    - 승인: snapshot·version·Issue open 확인 제거, 시작 알림 intent 없음, 자동 승인 자격 무시
    - 재시도: 중복·활성 work·닫힌 Issue 확인 제거, `reopened_from` 누락
    - 취소: incident를 두고 감, 결과 불명 work 취소 허용
    - 시작 게이트: 시작 알림 ACCEPTED·scope 재확인·취소 요청·RUNNING 슬롯·READY·incident NEW 확인 제거, deadline 값 틀림
    - 기타: 다른 incident 연결 기록 안 함, scope 변경에도 계속 진행
- 수용 기준:
  - T-STATE-01 / T-ISS-04: 동시 claim·중복 poll·동시 로그 → 활성 work 1, `WORK_STARTING` 1, attempt 최대 1: PASS
  - T-V4-02: retry 승인 중복 → 새 generation 1개(두 번째는 409 `already_retried`), 같은 키·다른 body → 409: PASS
  - snapshot hash 불일치 승인 → 409 `ISSUE_SCOPE_CHANGED`, 오래된 version → 409 `STATE_CONFLICT`: PASS
  - T-ISS-06(일부): closed Issue·요구 변경 → 시작 게이트에서 BLOCKED·충돌 보고, reopen·force-push 없음(사람 PR은 W23): PASS
  - terminal work에 새 로그 → evidence만 추가, 새 generation 자동 생성 없음: PASS
  - `start_attempt` 외 경로로 attempt를 만들 수 없음(코드 검색 테스트): PASS
- 판단: D78(승인 snapshot 비교 대상, scope 변경 상태별 처리, retry 중복 거절, 시작 전 취소의 incident, 슬롯 대기, work 조회 API·CLI 기대 값)
- 증거: 커밋은 이 보고를 포함한 W25 커밋
- 남은 일·위험:
  - 시작 알림 발송·receipt·60초 초과 차단(W26), agent token·workspace·adapter 연결(W28·W13), 시작 게이트의 Issue 재조회(EXT)는 W26·W28
  - 연결된 incident의 증거는 원래 incident에 남는다(조사 도구에서 연결 incident 조회는 W28)
- 다음 카드: W26 (fake 부분)

### W24 중단 보고 — live 부분 G2·G10 대기 (2026-09-27T10:33Z)

- 상태: UNIT_TESTED (live: BLOCKED_ON_HUMAN G2·G10). FakeGitHub와 실제 SQLite로 끝냈고, GitHub에는 읽지도 쓰지도 않았다
- 변경 파일:
  - `linemedic/control_plane/issue_router.py`:
    - `IssueRouter.route`(mirror 갱신 뒤 lookup 1~5), `lookup`, 신규 생성(intent → POST → 결과 기록), `route_pending`
    - `reconcile_create_issue`, `operator_bind`, `candidates`
    - Issue 제목·본문 템플릿과 marker, issue form 파서
  - `linemedic/contracts/github/linemedic-issue-form.yml`: 승인 issue form(G2에서 데모 repo에 복사, 시드는 바꾸지 않음)
  - `linemedic/control_plane/ops_api.py`: `GET /ops/issues/candidates/{incident_id}`, `POST /ops/incidents/{id}/issue-binding`(basis OPERATOR). `AppContext.issue_router`
  - `linemedic/cli.py`·`Makefile`: `make issue-bind INCIDENT_ID= ISSUE_NUMBER=`(D48: operator token으로 Control API 호출), `config [control_api]`(127.0.0.1:8080)
  - `linemedic/control_plane/detector.py`: `on_new_incident` hook(새 사건만, 트랜잭션 뒤). `issue_sync.py`: `upsert_mirror` 공개·`poll_once(wait_seconds)`. `notifications/blocker.py`: work 없는 보고
  - 테스트: `integration/test_issue_matching.py`(33), `live/test_issue_live.py` S4 2개(live_github)
- 실행 (로컬 개발 Mac, mock — live 아님):
  - `make test` → 1036 passed, 10 deselected / `make lint` → PASS
  - `make test-live` → S4 2개 skipped(쓰기 허락·후보 준비 표시 없음), S5·github smoke skipped
  - 변이 확인 27개(각각 넣으면 테스트가 실패했고, 확인 뒤 원래 코드로 되돌렸다):
    - lookup: binding 재조회 생략, 유사 후보 무시, 토큰 1개로 후보, mirror 완전성 무시, 갱신 실패 무시, 불명 intent 뒤 재생성, form 작성자 승인 무시, 다른 scope 봇 Issue 포함, receipt 검증 제거
    - 생성: 결과 불명을 실패로, shadow에서도 intent, rate limit을 에스컬레이션, FAILED 재시도에 새 intent, 생성 뒤 binding 안 함, 제목·본문 정제 제거
    - 조정: 작성자·marker·시각 범위 확인 제거, 0개를 채택처럼
    - 연결·API: 연결 Issue closed 무시, Issue 기반 사건도 route, 운영자 연결 version 검사 제거·PR 허용, 거절 뒤 멱등 기록 유지, 병합에도 hook, work 없는 차단 알림 제거
    - W23 refactor 뒤 W23 변이 30개도 다시 모두 잡혔다
- 수용 기준:
  - T-ISS-01: 기존 binding → 같은 번호 재사용·새 Issue 0개. 완전 조회·후보 없음 → Issue 1개·binding(CREATED)·work 1개: PASS
  - T-ISS-02: 제목만 비슷한 후보 2개 → AMBIGUOUS(생성·패치 0). 페이지 조회 실패 → LOOKUP_INCOMPLETE(생성 0). PR 항목은 후보가 아님: PASS
  - T-ISS-03: 생성 뒤 timeout → UNKNOWN, 두 번째 POST 0회, reconcile로 1개 채택. 다른 작성자가 같은 marker를 쓴 Issue → 채택하지 않음: PASS
  - 로그 본문 안의 `#번호`·HTML marker로 연결되지 않음: PASS
  - shadow 모드(`write_enabled=false`)에서 만들 Issue 계획만 출력: PASS
  - live S4-new·existing·ambiguous 각 1회 기록: NOT_RUN (G2·G10)
- 판단: D77(lookup 세부·form 형식·후보 규칙, closed 연결, attached, 생성 결과 매핑, 템플릿·marker, 조정 규칙, 운영자 연결 API, control_api 주소)
- 증거: 커밋은 이 보고를 포함한 W24 커밋. `evidence/S4-issue-live.md`는 실제 실행 전이라 없다
- 남은 일·위험:
  - G2·G10이 열리면 issue form을 데모 repo에 복사하고 live S4 3종을 실행한다(쓰기는 사용자 허락 뒤)
  - attached 로그 incident의 증거 연결과 retry(W25), CREATE_ISSUE 조정 CLI(W11), router·poll 루프 기동(W13)
  - 토큰 휴리스틱은 중복을 놓치거나 과하게 모호로 볼 수 있다(D77 대가)
- 다음 카드: W25

### W23 중단 보고 — live 부분 G2 대기 (2026-09-27T10:04Z)

- 상태: UNIT_TESTED (live: BLOCKED_ON_HUMAN G2). FakeGitHub와 실제 SQLite로 끝냈고, GitHub에는 읽지도 쓰지도 않았다
- 변경 파일:
  - `linemedic/control_plane/issue_sync.py`:
    - `IssueSync`(initial import·delta·전체 조회, 서버 시각 경계 checkpoint, ETag 304, rate limit backoff, `run` 루프)
    - `snapshot_sha256`(docs/03 §7)
    - 새 Issue → incident·work, 사람 작업·closed·권한 회수·삭제 처리, shadow `planned`
  - `linemedic/control_plane/supervisor.py`: `ensure_work`(generation·unique 재확인)·`check_human_work`(W25 카드 함수를 먼저 구현)
  - `linemedic/control_plane/ops_api.py`: `POST /ops/integrations/github/sync`(repo 지정 불가, 200·503 `LOOKUP_INCOMPLETE`·429·busy 409). `idempotency.abandon`, `AppContext.issue_sync`
  - `linemedic/cli.py`·`Makefile`: `make issue-sync RUN_ID=`(G2 env 없으면 NOT_CONFIGURED 종료 코드 2)
  - `linemedic/integrations/github.py`: 응답 `server_time`(Date 헤더), `list_pulls` 100개 페이지, Fake `update_issue`·`remove_issue`·`add_pull`·assignee ID·`repository_url`·요청 헤더 기록
  - 테스트: `integration/test_issue_polling.py`(39), `unit/test_github_port.py` Date 헤더 1개, `live/test_issue_live.py`(S5-new, live_github)
- 실행 (로컬 개발 Mac, mock — live 아님):
  - `make test` → 1003 passed, 8 deselected / `make lint` → PASS
  - `make test-live` → S5-new skipped(이번 실행 표시 없음), github smoke 2 skipped(NOT_CONFIGURED G2)
  - 변이 확인 30개(각각 넣으면 테스트가 실패했고, 확인 뒤 원래 코드로 되돌렸다):
    - 새 Issue 판정: 활성화 전 backlog 허용, 이미 닫힌 새 Issue 허용, 봇 Issue에 work, intake 꺼져도 work, 모든 작성자 신뢰, deny label 자동 승인
    - mirror·중복: PR 항목 mirror, 다른 repo 허용, poll_event_key 중복 제거 제거, snapshot에 updated_at 포함
    - 사람 작업: 사람 assignee 무시, 다른 사람 PR 무시, 봇 PR도 사람 작업, 다른 번호 참조 매칭
    - 변경 처리: closed 무시, 권한 회수 무시, 시작한 work도 차단 시도, cancel 요청 version 미증가, scope 재검사 제거, 삭제 감지 제거
    - checkpoint·조회: 실패 뒤 checkpoint 이동, 서버 경계 대신 읽은 최대 시각, cap delta를 서버 경계로, scope별 checkpoint 공유, 전체 조회 안 함, ETag 안 보냄, rate limit 대기 무시
    - 기타: 자동 승인 실패로 polling 중단, busy 멱등 기록 미삭제, sync run 불일치 허용
- 수용 기준:
  - T-ISS-05: 초기 backlog 50개 → 자동 work 0개, 미승인 작성자 새 Issue → `WAITING_APPROVAL`(자동 수정 0건), 다른 repo 데이터 없음: PASS
  - T-ISS-06(일부): closed Issue·사람 assignee·다른 사람 PR → 자동 작업 없음, 충돌 보고(`WORK_BLOCKED` intent, stage intake): PASS
  - PR 항목은 mirror에 없음, 10페이지(테스트는 3페이지) cap에서 잔여 페이지가 있으면 `complete=false`: PASS
  - 두 번째 페이지 조회 중 실패 → checkpoint 그대로, 재시작 뒤 이어 읽어도 work 중복 없음: PASS
  - bot 시작 댓글로 `updated_at`만 바뀌면 snapshot hash가 같고 새 work 없음: PASS
  - live S5-new(승인된 작성자의 새 Issue 1개를 polling으로 감지, 감지·생성 시각 기록): NOT_RUN (G2)
- 판단: D76(scope별 활성화, checkpoint 경계, 새 Issue 판정 시점, 사람 작업 판별, closed·권한 회수·삭제 처리, ETag·전체 조회·backoff, shadow, sync API)
- 증거: 커밋은 이 보고를 포함한 W23 커밋. `evidence/S5-new-issue-detect.md`는 실제 실행 전이라 없다
- 남은 일·위험:
  - G2가 열리면 `LINEMEDIC_LIVE_S5=1 make test-live`로 새 Issue 감지 1회를 실행한다(사람이 새 Issue를 만든다)
  - 자동 승인 전이·scope 재검사(W25), poll 루프 기동(W13), router가 만든 Issue의 mirror·work 선기록(W24)은 뒤 카드
  - overlap 안에 per_page×max_pages보다 많은 Issue가 한꺼번에 바뀌면 delta가 앞으로 가지 못한다(D76 대가)
- 다음 카드: W24 (fake 부분)

### W22 중단 보고 — live 부분 G2·G10 대기 (2026-09-27T09:33Z)

- 상태: UNIT_TESTED (live: BLOCKED_ON_HUMAN G2·G10). 포트·fake·설정·catalog·doctor까지 끝냈고, GitHub에는 읽지도 쓰지도 않았다
- 변경 파일:
  - `linemedic/integrations/github.py`:
    - `GitHubPort`, `GitHubBase`(등록 repo 고정 경로, 번호·branch·필터·since·label 검증, `write_enabled=false`면 `WritePlan`)
    - `HttpGitHub`(httpx. API 버전 헤더는 N11 전에는 보내지 않음), 오류 `RateLimited`·`Forbidden`·`NotFound`·`Conflict`·`Unknown(request_sent)`
    - `FakeGitHub`(페이지네이션·PR 섞인 목록·ETag 304·장애 주입 `fail_next`), `github_from_settings`
  - `config/linemedic.toml`·`common/config.py`: `[repository] service_id`, `[github]`(base_url·`write_enabled = false`·timeout, api_version 생략), route ID·adapter별 필드 검증, repo 이름의 `.`·`..` 조각 거부
  - `linemedic/control_plane/catalog.py`: 등록 repo·route·자동 처리 작성자(숫자 ID)·deny label. 모르는 repo·route와 꺼진 route 거부
  - `linemedic/scripts/doctor.py`: github 항목을 포트로 — repo 숫자 ID 일치 + 봇 identity(login·ID). W03 `default_get` 제거
  - 테스트: `unit/test_github_port.py`(78), `unit/test_catalog.py`(18), `unit/test_github_setup.py` doctor 5개 추가·수정, `helpers/github_contract.py`(unit·live 공통 계약), `live/test_github_smoke.py`(live_github)
- 실행 (로컬 개발 Mac, mock — live 아님):
  - `make test` → 963 passed, 7 deselected / `make lint` → PASS
  - `make test-live` → github smoke 2개 skipped(NOT_CONFIGURED G2: GITHUB_REPOSITORY_ID, GITHUB_REPOSITORY, GITHUB_BROKER_CREDENTIAL)
  - 변이 확인 21개(각각 넣으면 테스트가 실패했고, 확인 뒤 원래 코드로 되돌렸다):
    - 포트: 쓰기 차단 무시, 403 rate limit를 Forbidden으로, ReadTimeout의 `request_sent`, fake 부작용 뒤 실패 무시, 번호에 bool 허용, branch 중간 `..` 허용, Link next 무시, API 버전 헤더 항상 전송, 예외 원인 연결, 오류 메시지 비마스킹, since 검증 제거, PR 항목 표시 제거
    - catalog·설정: 꺼진 route 허용, 작성자 문자열 허용, repo ID 불일치 허용, smtp 수신 env 검사 제거, repo 이름 `.` 조각 허용, repo·intake 서비스 교차 검사 제거, `write_enabled` 기본값 true
    - doctor: repo ID 비교 제거, 봇 identity 확인 제거
- 수용 기준:
  - fake: 3페이지 목록·PR 항목 포함·두 번째 페이지 403(`Forbidden`)·생성 뒤 timeout(부작용 있음, `Unknown`)이 각각 정확한 오류 타입: PASS
  - 등록 repo 밖을 가리키는 요청을 만들 수 없음(메서드에 repo 인자 없음, 모든 요청 경로가 `/repos/<등록 repo>` 또는 `/user`): PASS
  - `write_enabled=false`에서 쓰기 호출 0회(Fake·HttpGitHub 모두): PASS
  - live: 전용 repo에서 조회·생성·댓글 각 1회 receipt: NOT_RUN (G2·G10)
- 판단: D75(HTTP 상태·전송 예외 매핑, shadow 계획, 설정·route 검증, catalog, doctor identity, live smoke 쓰기 이중 허락)
- 증거: 커밋은 이 보고를 포함한 W22 커밋. `evidence/N11-github-smoke.md`는 실제 실행 전이라 없다
- 남은 일·재개 조건: G2가 열리면 `make test-live`로 읽기 계약 확인 → G10과 사용자 허락 뒤 Issue·댓글 smoke 1회 → N11로 `github.api_version` 확정. GitHub App token을 쓰면 doctor identity(`/user`)가 FAIL로 나오므로 D46대로 봇 계정 credential을 쓴다
- 다음 카드: W23 (fake 부분)

### W09 독립 리뷰 반영 (카드 밖, 2026-09-27T09:17Z)

- 계기: W09 커밋(`dc50ee5`, PR #43) 뒤 독립 리뷰어가 브로커 변경분을 spec과 대조했다. 높은 심각도는 없었고, 실패 probe로 재현한 지적 4건을 고쳤다
- 수정 (코드를 소유한 브랜치에 커밋하고 뒤 브랜치로 병합, force push 없음):
  1. 중간 — B05 URL 검사 우회: `절차는https://…`처럼 한글 뒤에 붙은 URL, `www.`, `ftp://`가 통과해 초안에 남았다. W09 `_URL`을 단어 경계 없는 모든 scheme·`www.` 검사로 바꿨다. 같은 빈틈이 있던 W06 `sanitize.disable_urls`도 고쳤다(외부 출력용)
  2. 중하 — 수정 예산을 다 쓴 최종 거절(B03)의 WORK_BLOCKED 보고에 확인하지 않은 증거 ID가 들어갔다 → 이 run·사건에서 확인한 ID만 넣는다
  3. 낮음 — 이관 보고의 `symptom_impact`에 에이전트 요약이 들어갔다 → 사건 details의 관찰 사실을 넣고, 모델 요약은 `agent_summary`, host 사유 설명은 `reason_detail`로 따로 둔다(D74 ⑧ 보충)
  4. 낮음 — 본문에 아주 큰 정수가 있으면 422 대신 500이었다(B00 `loads_strict`) → StrictJSONError. 같은 부류로 1e999처럼 무한대가 되는 수도 거부한다
  - 추가: 제안 422 응답은 멱등 저장본(api_requests)에도 남으므로, 검증 오류 위치(key 이름)의 비밀 형태를 가린다. 한 제안 오류 뒤 다음 제안도 처리되는지 루프 테스트를 두 제안으로 보강했다
- 커밋: B00 `d87e076`, W06 `613533e`, W09 리뷰 반영 커밋, 사이 브랜치는 `앞 PR의 리뷰 수정 반영(병합)`
- 실행: W09 끝 `make test` → 862 passed / `make lint` → PASS, 리뷰어 probe 7개 모두 통과. 수정 전 새 테스트가 실패하는 것을 먼저 확인했다
- 판단: D74 ④(B05 URL 범위)·⑧(보고의 증상·요약·증거) 문구 보충

### W09 완료 보고 (2026-09-27T08:52Z)

- 상태: UNIT_TESTED (카드 목표 도달). 외부 쓰기 없음
- 변경 파일:
  - `linemedic/control_plane/broker/proposals.py`: 제안 schema(공통 필드 + `action` union 3종, extra=forbid·strict, category↔action·근거 규칙), 응답 모델 `ProposalReceipt`·`ProposalStatus`
  - `linemedic/control_plane/broker/intake.py`: 동기 접수 `submit`(principal 범위 → 멱등 → B01 → B02 → 제출 합산 → RECEIVED·VALIDATING → 202), 백그라운드 `Broker`(`run` 루프, `process_pending`, `recover_checking`, B03~B06, 거절·수정 1회·이관)
  - `linemedic/control_plane/broker/work_order.py`(승인 문구로 채운 초안, `delivery_status: not_sent`), `notifications/blocker.py`(spec 16 §6 blocker report payload)
  - `linemedic/control_plane/codes.py`(blocker_code·category↔action·run ID 형식 공용), `tools_api.py`(`submit_proposal`·`get_proposal`), `app.py`(`read_raw_body`·`safe_validation_errors`, `max_submissions`), `ops_api.py`(공용 코드 사용)
  - `linemedic/scripts/api_schema.py`·`Makefile`(`make api-schema`), `linemedic/contracts/api/proposal{,-receipt,-status}.schema.json`, DECISIONS.md·ADR.md(D74)
  - 테스트: `unit/test_proposal_schema.py`(82), `integration/test_proposal_intake.py`(55)
- 실행 (로컬 개발 Mac — 데모 호스트 아님):
  - `make test` → 843 passed, 5 deselected / `make lint` → PASS / `make test-docker` → 4 passed / `python -m linemedic.scripts.api_schema --check` → 최신
  - 실제 HTTP(uvicorn 127.0.0.1 + httpx, 임시 DB, 확인 뒤 삭제) 12/12 PASS: 202 응답은 `proposal_id`·`decision`만, 같은 키 재전송 → 같은 응답 바이트, operator token → 403, 128 KiB 초과 → 413, create_pr → REJECTED(PROTECTION_UNAVAILABLE)·`revision_allowed`, escalate 수정 제출 → ESCALATED/BLOCKED(UNSUPPORTED_ACTION)·제출 2회·WORK_BLOCKED intent PENDING 1개, attempt 종료 뒤 조회 404, `foreign_key_check` 빈 결과, DB 기록에 token 원문 없음
  - 변이 확인 25개: 시작 알림 ACCEPTED 무시, 멱등보다 B01 먼저, 형식 오류 미집계, B01 실패 집계, B03 incident 범위 무시, B04 UNKNOWN 무시, B05 secret·URL 검사 제거, B06 version 재확인 제거, catalog 매뉴얼 검사 제거, 예산 무시 수정 허용, 예산 판정 off-by-one, create_pr 통과, 초안 delivery_status·안내 문구 변경, get_proposal 범위 축소, recover_checking의 intent 무시, deadline·Issue snapshot·body ID 검사 제거, category↔action 검사 제거, escalate 증거 0개 거부, 루프의 복구·오류 계속·감사 내용 변경을 각각 넣으면 테스트가 실패했다. 확인 뒤 원래 코드로 되돌렸다
- 수용 기준:
  - T-V4-01: `schema_version: linemedic.v2` → 422, 다른 work·incident·attempt ID → 403, 이전 attempt token → 403: PASS
  - `equipment`+`create_pr`, `unknown`+`create_work_order_draft`, `code_bug`+`create_pr`+증거 0개 → 거절, `escalate`+증거 0개 → 허용(18조합 전체 표 시험): PASS
  - `actions: [...]`·`confidence`·역할/모델 필드 → 422, 중복 key → 422, 128 KiB 초과 → 413: PASS
  - 시작 알림이 ACCEPTED가 아닌(PENDING·SENDING·FAILED·UNKNOWN·없음) work의 제안 → 409 `START_NOTICE_UNCONFIRMED`: PASS
  - 같은 Idempotency-Key 재전송은 제출 횟수를 늘리지 않음(422·202 모두), 세 번째 서로 다른 제출 → 409: PASS
  - 초안에 자유 절차·제어값·URL·수신자 필드 → 422, 등록되지 않은 equipment·manual_ref(다른 서비스 설비 포함) → 422: PASS
  - draft 처리 후 `delivery_status: not_sent`, incident WORK_ORDER_DRAFTED(복구 상태 아님)·work HANDED_OFF·HANDOFF_DRAFTED intent: PASS
- 판단: D74(B01·B02 동기, 멱등 재전송 우선, B02 422도 제출 합산, 백그라운드 B03~B06 코드, 거절 후 수정 1회·이관, create_pr는 W10 전까지 PROTECTION_UNAVAILABLE, 초안·blocker report payload, JSON Schema 생성)
- 증거: 커밋은 이 보고를 포함한 W09 커밋. 실제 HTTP 확인은 임시 DB로 했고 삭제했다(제품 DB `runs/linemedic.db`는 건드리지 않음)
- 남은 일·위험:
  - create_pr의 패치 정책·runner 검사는 W10(지금은 항상 PROTECTION_UNAVAILABLE 거절), 봇 PR 생성은 W11
  - `Broker.run`을 API와 같은 프로세스에서 띄우는 것은 W13(`make start`), WORK_BLOCKED·HANDOFF_DRAFTED 실제 발송·문장 템플릿은 W26
  - B02 형식 오류는 proposal 행 없이 api_requests·감사 기록에만 남는다. 처리 중 예외가 난 제안은 다음 시작 전까지 CHECKING·VALIDATING에 머문다(감사 `PROPOSAL_CHECK_ERROR`)
- 다음 카드: W22 (fake 부분)

### W08 완료 보고 (2026-09-27T07:25Z)

- 상태: UNIT_TESTED (카드 목표 도달). 외부 쓰기 없음
- 변경 파일:
  - `config/linemedic.toml`·`common/config.py`: `[services.vision-inspection]`(코드 경로 없음), `[equipment.L3-CAM-1~3]`, 지표 이상 규칙(`detector.metric_*`), 설비 catalog 교차 검사
  - `linemedic/factory_sim/camera_metrics.py`(합성 시계열 30분·60 sample), `linemedic/control_plane/metrics_store.py`(run별 지표 보관)
  - `linemedic/control_plane/detector.py`: `MetricRule`·`metric_anomaly`(연속 3 sample)·`metric_signature`·`Detector.observe_metrics`(사건·비교 설비 증거)
  - `linemedic/control_plane/catalog.py`(서비스·설비·매뉴얼 범위, 같은 라인 서비스), `knowledge.py`(가상 매뉴얼 절 검색, 승인 문구 로드·검증)
  - `linemedic/factory_sim/manuals/MANUAL-L3-VISION-4.2.md`(가상 매뉴얼, 첫 줄 "실제 산업 매뉴얼·안전 절차가 아님"), `linemedic/policies/manual_templates.toml`
  - `linemedic/control_plane/tools_api.py`: `query_equipment_metrics`, `get_knowledge`, 설비 사건 symptom·scope, 같은 라인 서비스 배포. `deploys.py`: `deployed_at`·service·서비스 집합
  - `linemedic/factory_sim/scenarios.py`(`inject_s2_lite`), `linemedic/cli.py`(`scenario-s2-lite`), `Makefile`(`scenario-s2-lite RUN_ID= [RECENT_DEPLOY=1]`), DECISIONS.md·ADR.md(D73)
  - 테스트: `unit/test_camera_metrics.py`(22), `integration/test_s2_tools.py`(19)
- 실행 (로컬 개발 Mac — 데모 호스트 아님):
  - `make test` → 706 passed, 5 deselected / `make lint` → PASS / `make test-docker` → 4 passed
  - `make scenario-s2-lite RUN_ID=r-20260927-072354-84d8` → `INC-AC32E4E6A0AA`: vision-inspection NEW, `metric:L3-CAM-2:brightness_drop`, count 10, 증거 3(CAM-2 + 비교 CAM-1·3), 배포 기록 없음
  - `make scenario-s2-lite RUN_ID=r-20260927-072410-712e RECENT_DEPLOY=1` → `INC-793CE4A26CC2`와 이상 시작(07:19:40Z) 10분 전 mes-api 배포 기록(07:09:40Z). `foreign_key_check` 빈 결과
  - 변이 확인: 설비 범위 무시, 배포를 사건 서비스로만 한정, 연속 조건 off-by-one, 배포를 이상 뒤에 기록, 비교 설비 증거 제거, 증상에 원인 추정 문구를 각각 넣으면 테스트가 실패했다. 확인 뒤 원래 코드로 되돌렸다
- 수용 기준:
  - CAM-2 관찰 밝기 59·신뢰도 0.61, CAM-1(100·0.94)·CAM-3(99·0.93) 정상값이 표와 같다: PASS(도구 응답에서도 확인)
  - 미등록 `equipment_id`(L3-CAM-9·소문자·PLC-1) → 404, 다른 서비스 사건의 카메라 조회 → 404, 다른 사건 범위 → 404: PASS
  - `get_knowledge`가 매뉴얼 밖 경로·URL을 돌려주지 않음(`../../etc/passwd`·URL 검색어 → 빈 결과): PASS
  - recent-deploy 변형에서 `get_deploys`에 24시간 안의 mes-api 배포(이상 전), 기본 변형에서는 없음: PASS
  - 도구 응답·매뉴얼에 "렌즈 오염이 원인" 같은 확정 문구·시나리오 이름 없음: PASS
- 판단: D73(catalog 형식, 이상 규칙 세부, 조회 창, 같은 라인 배포 범위, `deployed_at`, recent-deploy 배포 시각)
- 증거: 커밋은 이 보고를 포함한 W08 커밋. DB·지표 파일은 git 제외 경로 `runs/` 아래에 있다
- 남은 일·위험:
  - 정비 초안 제안 접수와 승인 문구 채우기는 W09, sandbox 실제 실행은 W16
  - 같은 run에서 S1과 S2-lite를 섞으면 같은 라인의 배포 기록이 서로 보인다(평가는 run을 나눈다)
- 다음 카드: W09

### 전체 검증 보고 — B00~W07 (카드 밖, 2026-09-27T06:55Z)

- 요청: 지금까지 만든 것이 실제로 잘 작동하는지 확인
- 방법:
  - 새 clone에서 `make setup` → `make lint` → `make test` → `make test-docker`(로컬 상태 없이 재현)
  - 새 DB로 전체 흐름: `make run-new` → `make scenario-s1` → `make detect-once` → `make verify-negative`
  - 실제 uvicorn 서버에 HTTP 요청 26개(인증·범위·조회 도구·입력 검증·멱등성·전이 표)
  - verifier 실시간 경로: 저장소 밖 임시 정상 구현 이미지로 60초 관찰 PASS(60.05초, 표본 4/4, 관찰 로그 12줄), 관찰 중 t≈35초 컨테이너 제거 → INCONCLUSIVE/identity_changed(35.96초). 임시 이미지는 지웠다
  - DB `foreign_key_check`·`integrity_check`, token 평문 저장 여부, holdout 식별자 유출 여부, 남은 컨테이너·network, doctor·host-manifest·W02·W03 스크립트 종료 코드
  - 독립 리뷰 에이전트(읽기 전용)가 W04~W07 코드를 검토했고, 지적은 모두 재현 테스트로 확인한 뒤 고쳤다
- 발견·수정(각 PR 브랜치에 수정 커밋을 넣고 앞에서부터 병합 커밋으로 반영, force push 없음):
  - B00(#33): `loads_strict`가 깊은 중첩 JSON에서 RecursionError → `StrictJSONError`
  - W04(#37): 상대 `RUNS_DIR` mount 수정 백포트(#38에만 있던 수정)
  - W05 1부(#38): 마지막 표본이 60초를 넘기면 재관찰 없이 PASS하던 문제(HIGH), 잘못된 UTF-8 한 바이트에 로그 reader가 죽고 alive로 남던 문제(MEDIUM), 한 표본의 틀린 응답이 다른 case 무응답에 가려 INCONCLUSIVE가 되던 문제, 계약 하한(case 1개·표본 4회·관찰 60초 이상)
  - W06(#39): 멱등 재전송 응답 바이트 불일치(key 순서), 동시 첫 migration 충돌, INV-01 정적 검사 우회 3종 탐지
  - W05 2부(#40): 관찰 미완료 PASS 저장 거부, Ctrl-C 중단 시 INCONCLUSIVE 기록, 강제 종료로 남은 시험 사건 안내
  - W07: `detect-once`가 쌓인 로그를 한 번에 읽어 60초 규칙을 무시하고 재실행 때 다시 세던 문제 → Docker daemon 수신 시각과 checkpoint(D72 ⑧ 갱신)
- 결과: 각 브랜치에서 `make lint`·`make test` 통과. 최종 W07 브랜치 `make test` → 665 passed, `make test-docker` → 4 passed. 수정 전 실패를 확인한 회귀 테스트 20여 개 추가
- 남은 위험: detect-once의 60초 창은 실행 사이에 이어지지 않는다(상시 감시 W13). 실제 수정 PR로 60초 PASS까지 가는 흐름은 W10·W12 이후에 확인한다

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
