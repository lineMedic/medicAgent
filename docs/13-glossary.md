# 13. 용어집

| 용어 | 뜻 |
|---|---|
| Control Plane | LineMedic의 신뢰 영역. FastAPI + SQLite 단일 프로세스의 모듈 모음(detector, issue_sync/router, supervisor, broker, notifier, memory, release, verifier) |
| runtime 에이전트 | 제품 안에서 sandbox로 실행되는 Nemotron 기반 조사 에이전트. 코딩 에이전트와 다르다 |
| 코딩 에이전트 | 이 저장소를 구현하는 개발용 에이전트 (AGENTS.md의 독자) |
| MES | Manufacturing Execution System. 여기서는 합성 불량 집계 서비스 `mes-api` (`l3-mes-api` repo) |
| L3 | 가상 3라인 |
| run | 한 번의 실행·평가 단위 (`demo_runs`) |
| routing_scope | 티켓 처리 영역. `live` 또는 `eval:<run_id>` |
| incident (사건) | 한 run에서 관찰된 장애. 상태 10개 |
| work item | Issue 단위의 단일 작업. 승인·시작 알림·조사·리뷰 대기. 상태 11개 |
| generation | 같은 Issue에서 재시도할 때 올라가는 work 번호 |
| attempt | 한 번의 실제 모델 세션. deadline 240초·도구 15회 |
| problem_fingerprint | run을 뺀 정규화 오류 signature의 SHA-256. 중복 후보 키이지 원인 증명이 아님 |
| binding | 문제 fingerprint와 GitHub Issue의 확정 연결 (`issue_bindings`) |
| mirror | GitHub Issue의 로컬 사본 (`github_issues`) |
| checkpoint | polling이 마지막으로 완전히 저장한 기준 시각 (`integration_state`) |
| issue snapshot hash | 승인 관련 필드만 정규화한 hash. 시작 댓글로 바뀌지 않음 |
| 시작 게이트 (start gate) | 시작 알림 receipt가 ACCEPTED로 저장되기 전 writable workspace·attempt를 막는 host 검사 |
| receipt | 알림 공급자가 요청을 접수했다는 증거(댓글 ID, SMTP 응답). 사람이 읽었다는 뜻 아님 |
| outbox | 알림 intent를 상태 변경과 같은 트랜잭션에 저장하는 durable 테이블 (`notifications`) |
| execution / intent | 외부 부작용(CREATE_ISSUE·CREATE_PR·DRAFT_WORK_ORDER·DEPLOY) 기록. 호출 전에 INTENDED로 먼저 저장 |
| UNKNOWN | 외부 결과를 판단할 수 없는 상태. 재실행 금지, 재조회(reconcile)만 |
| reconcile | 외부 상태를 읽어 UNKNOWN을 푸는 일. 새 변경을 만들지 않음 |
| 논리 키 (logical key) | 같은 업무의 재수행을 막는 unique 키 |
| 멱등 키 (Idempotency-Key) | 같은 API 요청의 재전송을 식별하는 헤더. 다른 body면 409 |
| CAS | compare-and-swap. `WHERE version=? AND status=?` 조건부 UPDATE |
| broker | 모델 제안을 검사해 제한된 실제 조치(PR, 초안, 차단)로 바꾸는 모듈 |
| runner | 비신뢰 테스트를 실행하는 격리 컨테이너 (network none) |
| R0 / R1 / R2 | base 회귀 확인 / base+새 테스트 재현 실패 확인 / candidate 통과 확인 |
| candidate | 브로커가 patch를 적용해 만든 commit (`candidate_sha`, `candidate_tree`) |
| baseline 브랜치 | run별 PR 대상 `baseline/<run_id>` (보호 규칙 적용) |
| autofix 브랜치 | PR source `autofix/<run_id>/<incident_id>/<proposal_id>` |
| exact SHA 배포 | 사람이 승인한 최종 merge SHA만 정확히 빌드·배포 |
| identity chain | base → patch → candidate → PR head → merge SHA → image ID → container ID → contract hash 연결 |
| verifier | 업무 계약을 독립 프로세스에서 HTTP로 검사하는 신뢰 모듈. RESOLVED를 쓸 수 있는 유일한 경로 |
| 업무 계약 | `defect-summary-v1`. 불량 집계의 기대 응답과 관찰 조건 |
| observer | 검증 구간의 로그 스트림 연속성과 container·image 불변을 관찰 |
| holdout | 평가 runner만 쓰는 로트(L3-HOLDOUT-201). 기대값은 에이전트에 비공개 |
| S1b | 사람이 주입한 "HTTP 200이지만 틀린 집계" 결함. verifier 시험용 |
| 정비 요청 초안 (work order draft) | 설비 점검 필요를 담은 로컬 초안. 실제 발송하지 않음(`delivery_status=not_sent`) |
| blocker report | 진행 불가 이유·근거·해본 일·부작용·필요 조치·재개 조건 |
| case note | 과거 시도·결과를 검증 수준(outcome)과 함께 저장한 사례 |
| 정답노트 / 오답노트 | VERIFIED_SUCCESS / VERIFIED_FAILURE 사례. 권한 차단(BLOCKED)·PR-only(UNVERIFIED)와 다르다 |
| series / revision | 같은 사례의 버전 묶음 / 버전 번호. 수정은 새 revision |
| RETRACTED | 잘못된 자료로 확인돼 검색에서 제외한 사례 |
| memory snapshot | memory_assisted 실행에 허용한 note ID·revision·hash·cutoff의 불변 manifest |
| cold_start | 과거 사례를 주지 않는 기본 능력 평가 모드 |
| memory_assisted | 사전 고정 snapshot의 사례만 주는 평가 모드 |
| history projection | 과거 사례를 현재 incident가 인용할 수 있게 만든 새 evidence ID |
| lexical RAG | 키워드(FTS5) 검색 결과를 모델 문맥에 주고 인용하게 하는 방식. 임베딩 검색이 아님 |
| OpenShell | NVIDIA의 에이전트 sandbox. 파일·프로세스·네트워크 정책 |
| effective policy | provider가 추가한 규칙까지 포함해 실제 적용된 sandbox 정책 |
| NemoClaw / OpenClaw | 우선 후보 runtime (OpenClaw + OpenShell 레퍼런스 스택) |
| NAT | NeMo Agent Toolkit. runtime 대안 |
| mock-ot-sink | S3-C에서 실제 PLC 대신 쓰는 팀 소유 수신 서버 |
| 게이트 (G1~G12) | 사람이 열어야 진행할 수 있는 결정·권한·행동 |
| V4-CP0~CP5 | 원본 체크포인트 |
| core / hardening / P1 | 필수 / core 이후 보강(H03~H07) / 확장 |
