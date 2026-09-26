# 11. 개발 환경·실행·초기화 Runbook

> v4: 실행 일정은 [10](10-delivery-plan.md)의 실제 남은 시간·KST 체크포인트를 따른다. Issue·알림·사례 config를 추가했다. **목표 명령 계약이다.** 이 패키지에는 Makefile·Python 실행 코드·Docker 이미지가 들어 있지 않다. 구현자는 아래 명령을 구현하고 실제 성공한 명령/버전을 README에 기록해야 한다. 현재 바로 실행되는 제품으로 오해하지 않는다.

## 1. 사전 조건

킥오프에서 정한 **데모 호스트 한 대**를 사용한다([02](02-architecture.md) 1절). OpenShell·선택 런타임의 현재 지원 조합을 공식 문서와 실제 설치로 확인하고 host manifest에 기록한다. v3의 호스트 후보 목록을 검증 결과로 대신하지 않는다. 실제 운영망·OT 라우팅을 연결하지 않는다. runtime이 요구하는 OS/kernel/container 지원은 선택한 버전으로 확인한다. GPU나 클라우드 크레딧이 지급됐다고 가정하지 않는다. 예선 기본 추론은 확인된 NVIDIA cloud endpoint이며 오프라인 제품이 아니다.

| 항목 | 확인할 내용 |
|---|---|
| 호스트 | W00에서 확정한 1대. OS·kernel·CPU arch·메모리·디스크, 다른 운영 workload 없는가 |
| Python | 팀 공통 버전, runtime·FastAPI·pytest 호환, lockfile |
| Docker | 실제 Engine/Compose 버전, 고정 runner/base image 준비 |
| agent runtime | 선택한 한 가지 버전·설치 경로·작동하는 tool adapter |
| OpenShell | effective policy·host 지원·실제 denial 관찰 |
| NVIDIA | 모델 ID·endpoint·권한·tool calling 왕복 |
| GitHub | 조직 target repo, `baseline/*` 패턴 보호, 봇 credential 권한 범위, 봇이 아닌 reviewer |
| fixture·계약 | 버전 고정·host 전용 기대값·source에 정답 유출 없음 |

## 2. 환경 설정 이름

아래 값은 구현할 config 키다. 비밀은 `.env.example`에 값 없이 이름만 남긴다. 실제 환경 파일·run 원본은 Git ignore와 파일 권한을 설정한다.

```dotenv
LINE_MEDIC_ENV=demo
AGENT_MODE=sandbox
AGENT_RUNTIME=<openclaw-or-nat>
NVIDIA_MODEL_ID=<CP0에서 실제 호출 확인한 모델>
NVIDIA_BASE_URL=<CP0에서 확인한 endpoint>
NVIDIA_API_KEY=<secret-if-required>
GITHUB_REPOSITORY=<team-owner/l3-mes-api>
GITHUB_REPOSITORY_ID=<verified-numeric-repo-id>
ISSUE_INTAKE_ENABLED=true
ISSUE_POLL_SECONDS=60
ISSUE_TRUSTED_AUTHOR_IDS=<operator-approved-numeric-ids>
ROUTING_SCOPE=<live-or-eval:run-id>
NOTIFICATION_ROUTE_ID=github-issue-primary
LINEMEDIC_OPS_RECIPIENT=<optional-host-only-fixed-address>
SMTP_CREDENTIAL=<optional-secret-for-selected-mail-adapter>
MEMORY_MODE=cold_start
MEMORY_SNAPSHOT_PATH=<immutable-manifest-or-empty-for-cold>
CASE_SEARCH_ENGINE=sqlite_fts5
GITHUB_BROKER_CREDENTIAL=<GitHub App 설치 토큰 또는 봇 계정 credential, host-only>
GITHUB_SETUP_CREDENTIAL=<baseline 브랜치 생성용 운영자 credential, host-only>
DEMO_HOST_ID=<W00에서 확정한 호스트 식별자>
CONTROL_AGENT_TOKEN=<secret-run-scoped>
CONTROL_OPERATOR_TOKEN=<secret-host-only>
BASELINE_COMMIT=<full-sha>
RUNNER_IMAGE_ID=<verified-image-id>
MES_BASE_IMAGE_ID=<verified-image-id>
RUNS_DIR=<absolute-host-path>
```

agent와 runner에 위 환경 파일 전체를 mount하지 않는다. 필요한 최소 변수만 각 프로세스에 전달한다. model ID를 원안에 적힌 문자열이라는 이유만으로 존재·권한 확인 없이 고정하지 않는다.

## 3. 구현할 명령

| 명령 | 동작 | 금지 사항 |
|---|---|---|
| `make issue-sync RUN_ID=...` | 등록 repo의 bounded polling 1회, mirror·checkpoint 저장 | 임의 repo 또는 기존 backlog 전체 자동 시작 |
| `make issue-bind INCIDENT_ID=... ISSUE_NUMBER=...` | 운영자가 후보를 확인하고 binding 승인 | 모델이 보낸 URL로 연결 |
| `make approve-work WORK_ID=... EXPECTED_VERSION=...` | 정확한 Issue snapshot·범위 승인, 시작 알림 gate 진입 | 알림을 생략하고 patch 시작 |
| `make retry-work WORK_ID=... REASON=...` | 차단 요인 해소 확인, 새 generation·시작 알림 | 같은 attempt 무한 재실행 |
| `make notification-reconcile NOTIFICATION_ID=...` | 알려진 event의 provider 기록 조회 | 불명확한 알림 무조건 재발송 |
| `make rebuild-case-index` | 게시된 노트 revision의 파생 검색 인덱스 재생성 | DRAFT/RETRACTED를 정답으로 변경 |
| `make memory-snapshot RUN_ID=...` | 허용된 source note ID/hash와 cutoff를 불변 manifest로 저장 | 현재 holdout·미래 결과 포함 |
| `make setup` | lockfile 의존성, 고정 이미지·경로 준비, 사전 확인 | 실제 secret 출력, 운영 리소스 수정 |
| `make doctor` | 모델·GitHub·runtime·정책·fixture readiness 점검 | 실패를 경고만 하고 demo-ready로 표시 |
| `make run-new` | 새 run ID·manifest·보호 baseline branch 준비 | 원격 main 되돌리기 |
| `make start RUN_ID=...` | 해당 run의 control·MES·supervisor 준비; agent attempt는 시작 게이트 이후 | 다른 run 자동 정리 |
| `make scenario-s1 RUN_ID=...` | 버그 base 배포·로그 발생·감지 | agent 입력에 시나리오 정답 전달 |
| `make scenario-s2-lite RUN_ID=...` | 카메라 지표 이상·배포 혼동 flag 적용 | 실제 설비 접속 |
| `make verify-negative RUN_ID=...` | S1b 전용 시험 harness | 제품 broker에 우회 액션 추가 |
| `make security-test RUN_ID=...` | S3 계층별 시험·대조 기록 | production sandbox 정책 확대 |
| `make approve-release RUN_ID=... INCIDENT_ID=... WORK_ID=... PR_NUMBER=... MERGE_SHA=... EXPECTED_IMAGE_ID=...` | 정확한 승인 요청·릴리스·검증 | 최신 main 자동 선택·자동 머지 |
| `make reconcile RUN_ID=... EXECUTION_ID=...` | 실제 외부 상태 조회·기록 (`[core]` 사람이 실행, 자동 반복은 H04) | 새로운 PR/배포 생성 |
| `make export-run RUN_ID=...` | DB event·manifest·증거 정제본 내보내기 | 실행 원본 덮어쓰기 |
| `make reset RUN_ID=...` | 기존 run 종료·archive, 새 run 준비 절차로 연결 | DB 전체 삭제·force push·다른 리소스 prune |
| `make evaluate SUITE=...` | 정의된 평가 실행, 사람 승인 대기 표시 | 승인 bypass, 실패 row 누락 |

CLI는 인자 부족·다른 run/대상·권한 부족에 명확한 오류를 반환해야 한다. user-controlled 문자열을 shell에 삽입하지 말고 typed argv로 처리한다. `make approve-release`는 사람의 행동이며 무개입 지표에 포함시키지 않는다.

## 4. 첫 smoke 순서

1. 모델을 연결하지 않고 MES 정상·버그·S1b를 실행한다. verifier가 정상과 틀린 200을 구분하는지 확인한다.
2. 등록 repo에서 Issue 신규/기존 연결·work 선점·시작 알림 receipt를 먼저 확인한다. 이후 고정된 **사람 제안**으로 broker·PR·승인·exact release·검증을 연결한다. origin은 `manual_integration`이다.
3. 에이전트가 만든 원본 proposal로 같은 경로를 통과시킨다. 사람이 diff를 고치면 개입과 새 identity를 기록한다.
4. S2-lite의 실제 agent 요청 초안과 S3의 독립 시험을 수행한다.
5. 새 run에서 원본 workspace·fixture로 재현한다. cold_start에는 이전 정답 패치와 runtime state를 가져오지 않는다. memory_assisted만 승인한 과거 note snapshot을 명시적으로 선택한다.

## 5. 승인 체크리스트

- [ ] 사건·run·Issue·work·시작 알림 receipt·PR·검사 candidate가 연결돼 있다.
- [ ] 사람 리뷰 대상 head 이후 무관한 코드 변경이 없다.
- [ ] GitHub는 `merged=true`이고 최종 merge SHA를 읽었다.
- [ ] 현재 MES image가 승인 요청의 예상값과 같다.
- [ ] 최종 tree와 candidate tree가 같고 final 검사 결과가 있다.
- [ ] control 실행에 unknown·충돌·보호 실패가 없다.
- [ ] 정해진 합성 데모 환경에만 배포한다.

실제 리뷰어는 테스트만 보고 승인하지 않고 diff·근거·정비 가능성·허용 파일 범위를 확인한다.

## 6. 장애 대응

| 증상 | 확인 | 대응 |
|---|---|---|
| 새 Issue가 처리되지 않음 | initial cutoff·actor allowlist·deny label·checkpoint·페이지 완전성 | 정책상 대기와 수집 오류를 구분, 무조건 권한 확대 금지 |
| 동일 Issue 중복 작업 | routing_scope·binding·work unique·event key | 기존 작업 정지·조정, 자동으로 타인 PR 삭제 금지 |
| 작업 시작 알림 UNKNOWN | provider receipt·댓글 author/marker·송신 단계 | 접수 확인 전 writable workspace 금지 |
| blocker 보고가 화면에만 보임 | selected route·outbox·실제 provider 응답 | 외부 발송 미완료로 표시, 주소를 모델이 선택하지 않게 함 |
| 사례 검색 결과 없음 | cold mode·ACL·snapshot·index 지원·query normalization | NO_HIT/DISABLED/UNAVAILABLE 구분 |
| 틀린 사례가 정답으로 노출 | source verification·origin·publish status | 노트 철회·후속 revision·index 재구축, 원본 보존 |
| 모델 429/timeout | deadline·provider request ID | bounded retry, 안 되면 이관 |
| sandbox에서 tools API 불가 | 주소·proxy·binary·effective policy·인가 | 최소 허용 경로만 수정 후 대조 재시험 |
| reviewer 없이 merge 가능 | protection·bypass 허용 여부 | 배포 경로 중단, 규칙 수정·재시험 |
| 리뷰어가 승인할 수 없음 | PR 작성자가 리뷰어 본인 계정인가 | 봇 credential로 PR을 다시 열도록 설정 수정. 개인 PAT 사용 금지 |
| `baseline/<run_id>`에 보호가 안 걸림 | run_id에 `/` 포함 여부(패턴 `*`는 `/`와 불일치) | run_id 형식 수정 후 재생성 |
| PR 생성 timeout | 실행 intent·source/base/candidate | reconcile, 무조건 재생성 금지 |
| runner가 실패 | container exit/OOM/timeout·pytest 분류 | 환경 오류를 재현 성공으로 보지 않음 |
| 배포한 image가 다름 | host inspect·execution manifest | 검증 중단·UNKNOWN/충돌 기록 |
| HTTP 200인데 업무 실패 | case별 actual/expected | `ESCALATED`, 다음 패치 자동 생성 금지 |
| 로그가 안 옴 | current container·cursor·heartbeat | INCONCLUSIVE, ‘재발 없음’으로 처리 금지 |
| 토큰 노출 의심 | 공유본·runtime 전달 범위 | token 폐기/교체, 노출 흔적 정제, 관련 run 안전 상태 재검토 |

## 7. 초기화·반복 평가

먼저 새로운 사건 수집·dispatch를 정지하고 현재 execution을 확인한다. UNKNOWN이 있으면 reconcile하거나 그대로 미해결 기록을 남긴다. run manifest와 원본 증거를 export한 다음 해당 run의 **정확한 container ID·label·workspace 경로만** 정리한다. 범용 `prune`, 호스트 폴더 wildcard 삭제를 사용하지 않는다.

원격 baseline/autofix branch·Issue·PR, 원본 case note는 보존한다. 다음 run은 새로운 baseline branch와 코드 사본을 갖는다. 이전 run의 DB 기록은 보존하고 `active=0`으로 둔다. 과거 원본이 있는 경로에 새 결과를 덮어쓰지 않는다.

## 8. 새로운 개발자가 재현하려면

실제로 사용한 OS·버전·이미지 식별자·lockfile·설정 template·권한 확인 절차와, **성공한 명령의 실제 출력**을 README에 남긴다. API 키와 reviewer 없이 가능한 단계와 외부 서비스가 필요한 단계를 구분한다. 무인가 사용자가 제출 저장소를 볼 수 있는지, 심사자 접근은 어떤 방식인지 참가 조건에 맞춰 확인한다.

NVIDIA·GitHub 서비스가 안 되면 기록된 영상/trace를 보여줄 수 있지만, 그것을 당시 실시간 에이전트 실행으로 표시하지 않는다.


## 9. v4 실사용 전 점검

선택한 repo와 작성자만 자동 처리되는지, Issue 신규 등록이 실제 polling으로 감지되는지, 새 Issue를 bot가 생성해도 이중 작업이 되지 않는지 확인한다. 시작 알림 API의 접수시각과 실제 attempt 시작시각을 같은 시간 기준으로 기록한다. SMTP를 쓰면 서버 접수와 사람 inbox 수신을 구분한다.

종료·archive 전에 새 polling/dispatch를 중단하고, notification outbox의 SENDING/UNKNOWN과 외부 execution UNKNOWN을 확인한다. pending 결과를 버리지 않고 export에 포함한다. 새 run에서 과거 알림을 다른 Issue로 재전송하지 않는다. 실패한 알림의 재전송은 원래 route·Issue·logical key에 한정한다.

기존 v3 DB가 있다면 [18](18-migration-validation.md)의 전환 절차를 먼저 수행한다. fresh DDL을 원래 DB에 그대로 실행하거나 원격 main·Issue 이력을 초기화하지 않는다. 이 문서의 CLI 이름은 목표 계약이며 구현 확인 전 사용 가능한 명령이라고 안내하지 않는다.
