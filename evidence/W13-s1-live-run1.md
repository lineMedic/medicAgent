# W13 — S1 live run 1 (사람 제안, PR 생성까지)

| 항목 | 값 |
|---|---|
| 날짜 (UTC) | 2026-09-28T09:08Z ~ 09:15Z |
| 실행자 | 에이전트(Claude Code 세션). work 승인은 사용자의 명시 지시("PR 생성까지 진행")로 에이전트가 실행(아래 편차 참조) |
| 환경 | 개발 Mac(Darwin 25.6.0 arm64, Docker 29.8.0, Python 3.14.7) — 데모 호스트 아님(G1 미확정). `AGENT_MODE=local`, sandbox 없음(G5), runtime 없음(G4) |
| run | `r-20260928-090848-85c7`, config hash `1dcdb9fa337873017f67d677a5918b7562eee9a0d942e8450d967c6d09e024cc`(작업 트리의 `github.write_enabled = true`로 실행. 저장소 기본값은 shadow 모드로 둠) |
| 대상 repo | `lineMedic/l3-mes-api` (ID 1392050186), baseline `baseline/r-20260928-090848-85c7` @ `19045b62f292dedff24529cab505e6d86a91ed8c` (run-new가 CREATED) |
| 이미지 | MES `sha256:52663bf6e7a0422a07ac86d59547a14ee57d1faca9b63285cb8a149473b4b312`, runner `sha256:bd0afbb94c2187a4cf09eb34db0d93222088db04c257ec6e3af9c88d600b73c2` |
| 판정 | **PR 생성까지 PASS. 리뷰·머지(G7)·배포 승인(G8)·업무 검증은 NOT_RUN** |

## 명령

```bash
python -m linemedic.scripts.seed_demo_repo --output runs/seed-repo
git clone --bare runs/seed-repo runs/mirror/l3-mes-api.git        # 신뢰 mirror, main = 19045b62…
make run-new CREATE_BASELINE=1
make start RUN_ID=r-20260928-090848-85c7                            # 백그라운드
make scenario-s1 RUN_ID=r-20260928-090848-85c7
make approve-work WORK_ID=WORK-1A4F04C0A5D2 EXPECTED_VERSION=0 NOTE="사용자 명시 지시(2026-09-28 세션)로 에이전트가 실행한 운영자 승인"
make stop RUN_ID=r-20260928-090848-85c7
make export-run RUN_ID=r-20260928-090848-85c7
```

## 연결 (identity chain)

| 단계 | 값 | 시각 (UTC) |
|---|---|---|
| S1 주입 | 실제 MES 컨테이너: 불량 로트 500 ×3, 정상 로트 200 | 09:09 |
| 사건 | `INC-B707220FA44F` (mes-api, count 3) | |
| Issue 생성 | `EXE-FC5D6CCA8C12` CREATE_ISSUE SUCCEEDED → Issue #3 (basis CREATED) | 09:10:08 |
| work | `WORK-1A4F04C0A5D2` g1, WAITING_APPROVAL → 승인(`REQ-59520C5730CF`) → WAITING_NOTIFICATION | |
| 시작 알림 | `NOT-BE293890091A` WORK_STARTING ACCEPTED, 댓글 5866946931 (provider 접수 09:13:36, receipt 기록 09:13:36.543) | 09:13:36 |
| attempt | `ATT-D1DDC21BD80D` 시작 09:13:37.018 (receipt 기록 뒤), adapter `scripted`, **origin `manual_integration`**, 모델 호출 없음 | 09:13:37 |
| 제안 | `PROP-A5B6EA7445E1` ALLOWED — B01~B06·PATCH_POLICY·BASE·CANDIDATE·R0·R1·R2·CREATE_PR 13개 PASS | 09:13:37 |
| PR 생성 | `EXE-5CFF997ED7F2` CREATE_PR SUCCEEDED → PR #4, head `a8a30da7d2ceb0a1c7adc36c1946db187e02db20` (GitHub 조회 head SHA 일치), base baseline 브랜치, 본문 `Related to #3`·closing keyword 없음, 파일 `app/defects.py`(+1/−1)·`tests/repro/test_missing_inspector.py`(+15) | 09:13:5x |
| PR_READY 알림 | `NOT-D60186A05978` ACCEPTED, 댓글 5866951154 | 09:13:53 |
| 리뷰·머지 (G7) | NOT_RUN — 봇이 아닌 리뷰어 없음(D94) | |
| 배포 승인 (G8)·검증 | NOT_RUN — 기록 없음 | |

원본: `runs/r-20260928-090848-85c7/export/20260928T091450669005Z/shared/run-record.md`, 같은 폴더 `*.jsonl`(git 제외). 접수는 댓글 등록이다. 사람이 읽었다는 뜻이 아니다.

## 편차·한계

- 봇 credential이 개인 계정 `jgoneit`이다(D94). Issue·댓글·PR 작성자가 사람 계정과 같고, `github_setup_check`는 FAIL(봇=리뷰어, 봇 admin, `baseline/*` 보호 없음, 머지 방식 3개 모두 허용)이다. 배포 승인 서버는 봇이 아닌 리뷰어의 승인을 요구하므로 `jgoneit` 자신의 승인으로는 G8을 통과하지 않는다.
- work 승인은 사람 단계지만 사용자가 이 세션에서 "PR 생성까지 진행"을 명시 지시해 에이전트가 실행했다. 무개입 지표에 넣지 않는다.
- 제안은 사람이 미리 쓴 `linemedic/eval/manual_proposals/s1_manual.json`(origin `manual_integration`)이다. 실제 Nemotron agent의 제안이 아니므로 에이전트 성과로 합산하지 않는다.
