# W13 — S1 live run 2 (사람 제안, 사람 리뷰·머지·배포 승인 → RESOLVED)

| 항목 | 값 |
|---|---|
| 날짜 (UTC) | 2026-09-28T10:24Z ~ 10:45Z |
| 실행자 | 에이전트(Claude Code 세션)가 run·주입·work 승인(사용자 명시 지시)·감시·기록. **사람**: PR 리뷰·머지(G7, `daejung-kim96`), 배포 승인(G8, 사용자가 자기 터미널에서 `make approve-release` 실행·`approve` 입력) |
| 환경 | 개발 Mac(Darwin 25.6.0 arm64, Docker 29.8.0, Python 3.14.7) — 데모 호스트 아님(G1 미확정). `AGENT_MODE=local`, sandbox 없음(G5), runtime 없음(G4) |
| run | `r-20260928-102418-c75e`, routing scope `eval:r-20260928-102418-c75e`, config hash `1dcdb9fa337873017f67d677a5918b7562eee9a0d942e8450d967c6d09e024cc`(작업 트리의 `github.write_enabled = true`로 실행, 저장소 기본값은 shadow 유지) |
| 대상 repo | `lineMedic/l3-mes-api` (ID 1392050186), 봇 credential = `jgoneit`(D94) |
| 판정 | **PASS — 사건 RESOLVED, work SUCCEEDED, 업무 검증 PASS (origin `manual_integration`)** |

## 명령

```bash
# 준비: run 1 뒤 S4 시험 후보 Issue #5·#6·#7(에이전트가 만든 것)을 닫았다(같은 토큰으로 AMBIGUOUS가 되지 않게)
make run-new CREATE_BASELINE=1
make start RUN_ID=r-20260928-102418-c75e                               # 백그라운드, G8까지 유지
make scenario-s1 RUN_ID=r-20260928-102418-c75e
make approve-work WORK_ID=WORK-0E76170C9B2A EXPECTED_VERSION=0 NOTE="사용자 명시 지시(2026-09-28 세션, run 2)로 에이전트가 실행한 운영자 승인"
# 사람(G7): daejung-kim96이 PR #10 승인 → 머지
# 사람(G8): make approve-release RUN_ID=r-20260928-102418-c75e INCIDENT_ID=INC-2B98DD9DFD7E WORK_ID=WORK-0E76170C9B2A \
#   PR_NUMBER=10 PROPOSAL_ID=PROP-323E2D7C1954 MERGE_SHA=d9fae3861d1e288a2c4ae15c37620afad18e4e15 \
#   EXPECTED_IMAGE_ID=sha256:52663bf6e7a0422a07ac86d59547a14ee57d1faca9b63285cb8a149473b4b312   → approve
make stop RUN_ID=r-20260928-102418-c75e
make export-run RUN_ID=r-20260928-102418-c75e
```

## 연결 (W13: run-record)

| 단계 | 값 | 시각 (UTC) |
|---|---|---|
| S1 주입 | 실제 MES 컨테이너: 불량 로트 500 ×3, 정상 로트 200 | 10:24 |
| 사건 | `INC-2B98DD9DFD7E` (mes-api, count 3) | |
| Issue 생성 | `EXE-FE1AFD3C20E8` CREATE_ISSUE SUCCEEDED → Issue #9 | 10:24:45 |
| work | `WORK-0E76170C9B2A` g1 WAITING_APPROVAL → 승인 → WAITING_NOTIFICATION | |
| 시작 알림 | `NOT-692F48FAF36A` WORK_STARTING ACCEPTED, 댓글 5868093715 | 10:26:13 |
| attempt | `ATT-CF90AC6941EF`, adapter `scripted`, origin `manual_integration`, 모델 호출 없음 | 10:26 |
| 제안 | `PROP-323E2D7C1954` ALLOWED | |
| PR 생성 | `EXE-4B380FD69226` CREATE_PR SUCCEEDED → PR #10, 본문 `Related to #9`·closing keyword 없음, 파일 `app/defects.py`(+1/−1)·`tests/repro/test_missing_inspector.py`(+15) | 10:26:20 |
| PR_READY 알림 | `NOT-FEF361622B71` ACCEPTED, 댓글 5868097891 | 10:26:28 |
| 리뷰 (G7) | `daejung-kim96`(ID 132763253, 봇 아님) APPROVED, commit `7da4017a…` | |
| 머지 (G7) | `daejung-kim96`, merged=true, merge SHA `d9fae3861d1e288a2c4ae15c37620afad18e4e15`, **merge commit(부모 2) — squash 아님** | 10:38:18 |
| 배포 승인 (G8) | 사용자가 체크리스트 확인 뒤 `approve` → `REQ-A82A14652C69`, `EXE-2CB6298CF0DC` INTENDED → fetch → recheck → build → deployed SUCCEEDED | 10:43:46 ~ 10:43:52 |
| 업무 검증 | `VER-97453F5110BE` PASS(`all_checks_passed`, observation_complete, resolved_written), origin `manual_integration`, contract `defect-summary-v1` | 10:43:53 ~ 10:44:53 |
| 결과 알림 | `NOT-184923C23E25` RECOVERY_VERIFIED ACCEPTED, 댓글 5868345400 | 10:44:57 |
| 최종 | incident RESOLVED, work SUCCEEDED (verifier 경로) | |

## identity chain (W12)

| 고리 | 값 |
|---|---|
| base | `19045b62f292dedff24529cab505e6d86a91ed8c` (tree `e6718ce7deb861efd2d4916cbd27ef3078c621e3`) |
| patch | sha256 `863c9ea9a4be02c68a589f88c2cd9450a3f4c1c5faa271fc29cc2995d61aa679` |
| candidate | `7da4017a14ec4e7ab6d997cde0d0f420a4c82af0` / tree `8aec218e34a2e6731d95b0a1011d6dd3689cb2b5` |
| PR head | `7da4017a14ec4e7ab6d997cde0d0f420a4c82af0` (GitHub 조회, = candidate) |
| merge SHA | `d9fae3861d1e288a2c4ae15c37620afad18e4e15` |
| merge tree | `8aec218e34a2e6731d95b0a1011d6dd3689cb2b5` (= candidate tree, 신뢰 mirror에서 다시 계산한 final tree도 같음) |
| repro tree | `29518ccb55bff63a8c338fd670fa3def218b0c98` |
| image | `sha256:c45f4681fc87da283b456b64917636152ec62fee3bfa0cf268ac28f03d68ced8` (tag `linemedic-mes:release-r-20260928-102418-c75e-d9fae3861d1e`, 신뢰 레시피 빌드) |
| container | `cfbb244135c0f0a099af529f4f0d17a8acb1496e9442d948390adf1c4b611a1e` |
| contract | `defect-summary-v1` sha256 `0334df2662cdb121064bdc6e34b016980b497afb17d9b53b916e03d0c0bfc87f` |
| 배포 전 image | `sha256:52663bf6e7a0422a07ac86d59547a14ee57d1faca9b63285cb8a149473b4b312` (복원 명령은 execution 기록에 있음) |

원본: `runs/r-20260928-102418-c75e/export/20260928T104541723508Z/shared/run-record.md`, 같은 폴더 `executions.jsonl`·`verifications.jsonl`·`notifications.jsonl`(git 제외). 접수는 댓글 등록이다. 사람이 읽었다는 뜻이 아니다.

## 편차·한계

- 봇 credential이 개인 계정 `jgoneit`이다(D94). 봇 PR·Issue·댓글 작성자가 사람 계정이다. 리뷰·머지는 다른 팀원 `daejung-kim96`이 했다.
- 머지 방식이 squash가 아닌 merge commit이다(repo가 세 방식을 모두 허용, W03 setup check FAIL). 서버 검사는 merge tree = candidate tree로 통과했다.
- work 승인은 사람 단계지만 사용자의 명시 지시로 에이전트가 실행했다. 무개입 지표에 넣지 않는다.
- 제안은 사람이 미리 쓴 `linemedic/eval/manual_proposals/s1_manual.json`(origin `manual_integration`)이다. 실제 Nemotron agent 성과가 아니며, 검증 PASS도 에이전트 성과 분모에 넣지 않는다.
- sandbox 없는 local 모드, 개발 Mac(데모 호스트 아님)이다.
