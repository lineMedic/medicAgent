# W12 — 사람이 승인한 exact SHA 배포와 업무 검증 연결

| 항목 | 값 |
|---|---|
| 등급 | core (`build_recipe_sha256`만 H05) |
| 자율성 | C(G7·G8) — 사전 검사·배포 로직은 fake/docker로 UNIT_TESTED, 실제 승인 배포는 사람 |
| 선행 | W05, W11 |
| 목표 상태 | LIVE_VERIFIED |
| 원본 근거 | [spec 08 §1·§2·§3·§9](../spec/docs/08-release-verification.md), [spec 03 §5 릴리스 요청](../spec/docs/03-api-contracts.md), [spec 11 §5](../spec/docs/11-runbook.md), [spec 14 W03](../spec/docs/14-decisions-sources.md#w03) |
| 참조 | [docs/05 ⑥](../docs/05-workflows.md), DECISIONS D47 |
| 요구·테스트 | FR-07, FR-08, INV-02 / T-SOURCE-01~03, T-AUTH-01(`/ops/releases`) |

## 목표

운영자가 PR 번호·최종 merge SHA·현재 image를 명시해 승인하면, 그 SHA만 정확히 가져와 재검사·빌드·기동하고 image·container identity를 기록한 뒤 verifier에 넘긴다. 검사한 대상 → 승인한 대상 → 실행 대상이 끊김 없이 연결된다.

## 만들 파일

- `linemedic/control_plane/release.py`
  - 사전 검사 7단계([spec 08 §2](../spec/docs/08-release-verification.md)): operator·expected incident version·`PR_OPENED` / repo·PR·base branch가 catalog와 일치 / GitHub에서 `merged=true`·실제 head·최종 merge SHA 조회(머지 전 `merge_commit_sha` 사용 금지) / reviewer가 본 head = broker candidate, 최신 변경 승인 설정 기록 / 최종 tree == candidate tree / 현재 runtime image == `expected_current_image_id`(다르면 `SOURCE_CHANGED`) / 같은 논리 작업의 execution이 있으면 기존 상태 반환
  - 실행: `TX{ DEPLOY INTENDED(deploy:<work>:<sha>), incident DEPLOYING, work WAITING_VERIFICATION, run-level lock }` → trusted mirror에서 exact SHA fetch·checkout(main 최신 아님) → R1·R2 재실행(W10) → `mes.Dockerfile`로 빌드(repo의 Dockerfile·hook 미사용) → 이전 container·image ID 기록 → image **ID**로 새 container 기동(라벨 run/incident/execution, 내부 network, fixture read-only mount) → `docker inspect`로 확인 → `TX{ SUCCEEDED, incident VERIFYING, verification RUNNING }` → verifier(W05)
  - 실패 처리([spec 08 §3](../spec/docs/08-release-verification.md)): 검사·빌드 실패 → 기존 환경 유지 사실 확인 후 이관. 기존 서비스 중단 후 새 기동 실패 → 실제 상태 기록·이관(자동 rollback 없음). 배포 timeout → UNKNOWN. 다른 변경 감지 → 덮어쓰기 금지
  - `RepoDigest`가 없으면 local image ID만 기록
  - reconcile의 DEPLOY 분기(실제 container·image 조회)
  - 사람용 복원 절차 출력(이전 image ID로 기동하는 명령). 복원 후에도 검증 PASS 전에는 RESOLVED 아님
- `ops_api.py` 추가 — `POST /ops/releases`(docs/04 §4 body, 멱등성 필수)
- CLI·Makefile — `make approve-release RUN_ID= INCIDENT_ID= WORK_ID= PR_NUMBER= MERGE_SHA= EXPECTED_IMAGE_ID=`. 실행 전에 [spec 11 §5](../spec/docs/11-runbook.md) 승인 체크리스트를 화면에 보여 준다
- 테스트: `integration/test_release_checks.py`, `integration/test_release_docker.py`(docker, 테스트 fixture commit 사용)

## 수용 기준

- T-SOURCE-01: 검사 뒤 PR head가 바뀜 → 배포 거부.
- T-SOURCE-02: 최종 tree ≠ candidate tree(사람 추가 수정) → 기존 PASS 무효, 재검사·재승인 요구.
- T-SOURCE-03: unmerged PR 또는 test merge SHA → 거부.
- T-AUTH-01: agent token으로 `/ops/releases` → 403, 외부 변경 없음.
- `expected_current_image_id` 불일치 → 409 `SOURCE_CHANGED`.
- 같은 승인 재전송 → 같은 execution, 두 번째 배포 없음.
- docker: fixture commit으로 빌드·기동 후 inspect한 image ID가 execution 기록과 같다.
- live(G7·G8): 사람이 머지·승인한 실제 PR로 identity chain 전체(base → patch → candidate → PR head → merge SHA → tree → image → container → contract hash)가 run-record에 채워진다.

## 금지·함정

- 머지 감시 polling·webhook, 자동 머지, 최신 main 자동 선택을 만들지 않는다.
- 앱의 `/version` 응답으로 identity를 확인하지 않는다.
- `make approve-release`를 에이전트가 실행하지 않는다(G8). 무개입 지표에 넣지 않는다.
