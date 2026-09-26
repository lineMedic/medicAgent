# W10 — 패치 정책·candidate 생성·격리 runner R0/R1/R2

| 항목 | 값 |
|---|---|
| 등급 | core |
| 자율성 | A (runner 실행 테스트는 `docker` 마커) |
| 선행 | W04, W09 |
| 목표 상태 | UNIT_TESTED (`make test-docker` 포함) |
| 원본 근거 | [spec 06 §1~§5](../spec/docs/06-broker-runner.md), [spec 07 §5](../spec/docs/07-security.md), [spec 14 W05·W07·W08](../spec/docs/14-decisions-sources.md#w05) |
| 참조 | [docs/07 §1·§2](../docs/07-constants.md), [docs/05 ⑤](../docs/05-workflows.md), DECISIONS D47 |
| 요구·테스트 | FR-04, FR-05, INV-03, INV-04 / T-PATCH-01~03, T-REPRO-01~03, N06(runner 부분) |

## 목표

`create_pr` 제안의 diff가 허용 파일·크기·형태 정책을 통과하면, 서버 소유 disposable checkout에 적용해 candidate commit을 만들고, network none 컨테이너에서 R0(base 회귀)·R1(재현 실패)·R2(candidate 통과)를 실제로 실행해 판정한다.

## 만들 파일

- `linemedic/policies/broker_policy.toml` (D60) — 허용 파일(`app/defects.py`), 신규 테스트 glob(`tests/repro/test_*.py`), 파일 수 2, 줄 수 100, 보호 glob([docs/07 §1](../docs/07-constants.md))
- `linemedic/control_plane/broker/patch_policy.py`
  - diff 파싱 단계에서 거부: 절대경로, `..`, NUL, 역슬래시, 정규화 우회, symlink·submodule·mode 변경·binary·rename·삭제, 허용 밖 경로, 신규 테스트 2개 이상, 기존 테스트 수정, 상한 초과
  - 적용 **후** Git tree를 다시 읽어 변경 경로·mode·blob hash를 재확인(문자열 prefix 검사만으로 끝내지 않음), 보호 파일 hash 불변 확인
- `linemedic/control_plane/broker/candidate.py` — trusted mirror(`RUNS_DIR/mirror/l3-mes-api.git`)에서 base SHA의 깨끗한 사본을 `RUNS_DIR/<run>/checkouts/<proposal>/`에 만든다. credential·hook·global git config를 쓰지 않는다(`GIT_CONFIG_GLOBAL=/dev/null`, `core.hooksPath` 비움). `git apply --check` → `git apply` → 서버가 commit(작성자·시각 고정) → `candidate_sha`, `candidate_tree`, `patch_sha256` 기록. 모든 git 호출은 고정 argv
- `linemedic/runner/runner.Dockerfile` — MES 의존성 + pytest 고정 버전, non-root 사용자
- `linemedic/runner/pytest-protected.ini` — repo 설정 무시, `-p no:cacheprovider`, plugin autoload 차단(`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`), `--noconftest`
- `linemedic/control_plane/broker/runner.py` — `run_stage(stage, checkout, tests)`: `docker run --rm --network none --read-only --tmpfs /tmp:size=64m --cpus 1 --memory 512m --pids-limit 64 --cap-drop ALL --security-opt no-new-privileges --user <uid>:<gid>` + 읽기 전용 checkout mount + 결과 전용 mount, 단계 60초 timeout(초과 시 컨테이너 강제 종료·제거), 로그 1 MiB 상한. container ID·image ID·exit·OOM·timeout·raw log 기록. `--junitxml`을 결과 mount에 쓰게 하고 안전하게 파싱(크기 제한, XML 엔티티 비활성)
- 판정 함수 `judge_r1(exit, junit)`·`judge_r2(...)` — [docs/07 §2](../docs/07-constants.md) 규칙. Docker 125/126/127 분리
- `broker/intake.py` 변경 — `create_pr` 분기를 `PROTECTION_UNAVAILABLE`에서 실제 검사로 교체. 실패 시 check code 저장, 수정 예산이 남으면 incident `VALIDATING → INVESTIGATING`, 아니면 ESCALATED/BLOCKED + case event(BLOCKED, phase=validation)
- `make runner-image`, doctor 항목 `runner_image`(image ID 고정 확인)
- 테스트: `unit/test_patch_policy.py`, `unit/test_repro_judgement.py`(준비된 junit XML·종료 코드 조합), `integration/test_runner_docker.py`(docker). 정답 diff 등 테스트 입력은 `linemedic/tests/fixtures/patches/`에만 둔다

## 수용 기준

- T-PATCH-01: `tests/regression/*` 수정, `Dockerfile`·`pyproject.toml`·`.github/*`·`conftest.py` 변경 → 거부.
- T-PATCH-02: `../x`, `/etc/x`, symlink, binary, rename → 거부.
- T-PATCH-03: 파일 3개, 101줄 → 거부.
- T-REPRO-01: base에서도 통과하는 새 테스트 → `REPRO_NOT_FAILING`.
- T-REPRO-02: import 오류(exit 2), 미수집(exit 5), timeout, OOM, skip만 있는 테스트 → 재현으로 불인정.
- T-REPRO-03: R1 통과 후 candidate에서 새 테스트 실패 또는 보호 회귀 실패 → PR 생성 없음.
- docker: runner 컨테이너 안에서 외부 네트워크 접속이 실패하고, `docker inspect`로 network none·read-only·자원 제한이 확인된다(N06 runner 부분을 evidence에 기록).

## 금지·함정

- 사용자·에이전트가 보낸 경로에서 git 명령을 실행하지 않는다.
- 로컬 테스트 결과(`local_test_observation`)를 증거로 쓰지 않는다. 브로커가 다시 실행한다.
- 테스트 PASS를 "악성 코드 없음"으로 표현하지 않는다.
- 컨테이너를 만드는 범용 API를 노출하지 않는다. image·mount·argv·limit은 모두 서버 catalog에서.
