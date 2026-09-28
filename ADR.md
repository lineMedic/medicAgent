# ADR — 아키텍처 결정 통합 색인

> **문서 지위**: 결정의 통합 색인이며 정본이 아니다. **현재 상태**: 코드 없음 — 결정이 있다고 해당 기능이 구현된 것은 아니다.
> 결정의 상세 기록(이유·대가·영향 문서·재시험)은 원래 위치에 있다.
> - D01~D41: [spec/docs/14-decisions-sources.md](spec/docs/14-decisions-sources.md) — §3(v2·v3 결정 D01~D27), §7(v4 결정 D28~D41). 수정하지 않는다.
> - D42~: [DECISIONS.md](DECISIONS.md) — spec이 비워 둔 기술 선택과 구현 중 결정.
>
> 새 결정은 DECISIONS.md에 적고, **같은 변경에서 이 색인에 한 줄을 추가**한다. 색인과 원본이 다르면 원본이 맞다([AGENTS §8](AGENTS.md)).
> 관련 개요: [PRD.md](PRD.md) · [SPEC.md](SPEC.md) · [ARCHITECTURE.md](ARCHITECTURE.md)

## 1. 상태 정의

| 상태 | 뜻 |
|---|---|
| `유효` | 현행 기준. 구현이 따라야 한다 |
| `계승` | v2·v3에서 정한 결정. spec 14 머리말대로 역사 기록이지만, 현행 규범 문서(spec 00~17)에 반영된 범위에서 계속 적용된다 |
| `대체됨 → Dxx` | 뒤의 결정이 바꿨다. 원본 행은 지우지 않는다 |
| `기본값` | DECISIONS.md가 문서 작성 시점에 정한 기본값. 게이트 결과와 충돌하면 새 결정으로 바꾼다 |
| `게이트 대기` | 사람 게이트나 스파이크 결과가 있어야 확정된다([§4](#4-게이트-대기-결정)) |

## 2. 색인

영역 약어: **범위**(범위·일정·평가), **입력**(로그·Issue intake·work), **알림**, **기억**(사례 기억·검색), **보안**(권한·격리), **배포**(PR·릴리스·검증), **기반**(저장소·스택·코드 규칙).

### 2.1 v2 결정 (D01~D17) — spec 14 §3

| ID | 결정 한 줄 | 영역 | 상태 |
|---|---|---|---|
| D01 | S2-lite·S1b를 P0(현재 core) 시나리오로 올리고 full 정비 흐름은 삭제 | 범위 | 계승 |
| D02 | 설비 분기는 로컬 정비 요청 초안만 만든다. 복구 실적이 아니다 | 범위 | 계승 |
| D03 | 자동 머지 감시·변경 창 대신 사람의 명시적 exact SHA 배포 명령 | 배포 | 계승 |
| D04 | base → candidate → PR head → merge → image → verification 추적, 충돌 시 중단 | 배포 | 계승 |
| D05 | reset은 main을 되돌리지 않고 run별 보호 baseline branch를 쓴다. 과거 run·PR 보존 | 배포 | 계승 |
| D06 | 수정 범위는 `app/defects.py` + 새 repro 테스트 파일 1개 | 배포 | 계승 |
| D07 | 재현·회귀·업무·조치 적절성 검사를 구분하고, 재현 gate가 원인 판별을 보장한다고 주장하지 않음 | 배포 | 계승 |
| D08 | evidence ID는 존재·사건 소속·버전·시간의 출처 연결만 보장. 의미 타당성은 별개 | 입력 | 계승 |
| D09 | 생성 코드와 실행 컨테이너는 비신뢰 영역 | 보안 | 계승 |
| D10 | 권한은 서버 측 principal·scope로 판단. 경로·클라이언트 actor를 근거로 쓰지 않음 | 보안 | 계승 |
| D11 | 단일 worker라도 짧은 DB 트랜잭션·CAS·intent·reconcile로 동시성 처리 | 기반 | 계승 |
| D12 | verifier만 RESOLVED로 전이. 수동 resolve·자동 재조사 없음 | 배포 | 계승 |
| D13 | runtime 하나, critic 에이전트는 P1, confidence 기반 승인·강등 삭제 | 범위 | 계승 |
| D14 | 연결 실패를 정책 차단으로 단정하지 않음. 대조 연결 + 거절 기록 + 실패 주체 분리 | 보안 | 계승 |
| D15 | runtime 선택과 대회 조건(R1/R2) 인정을 분리. 답변 없이 충족을 주장하지 않음 | 범위 | 계승 |
| D16 | 사람 통합(manual integration)과 실제 agent E2E를 별도 체크포인트로 | 범위 | 계승 |
| D17 | DB `audit_events`가 원본, JSONL은 export. tamperproof 시스템이라고 하지 않음 | 기반 | 계승 (테이블 수는 v4 DDL [spec 04 §5](spec/docs/04-data-state.md)가 기준) |

### 2.2 v3 결정 (D18~D27) — spec 14 §3 "v3 변경 결정"

| ID | 결정 한 줄 | 영역 | 상태 |
|---|---|---|---|
| D18 | P0를 core / hardening(H01~H07)으로 분리. hardening 미완료는 "설계됨/미구현"으로 공개 | 범위 | 계승 (H01·H02는 D35로 core 승격) |
| D19 | KST 절대 일정과 체크포인트 | 범위 | 계승 (v4는 D41의 남은 시간·증거 CP. 목표 시각은 G11) |
| D20 | sandbox 안 에이전트 실행을 core로, hardening보다 먼저 | 보안 | 계승 |
| D21 | GitHub App/봇 계정이 PR 작성, 다른 팀원이 리뷰, `baseline/*` 패턴 보호, squash 단일 머지, run_id에 `/` 금지 | 배포 | 계승 |
| D22 | S3-C는 호스트 대조 + 같은 sandbox 허용/금지 + 거절 로그. 거절 로그 없는 실패는 UNATTRIBUTED | 보안 | 계승 |
| D23 | 데모 호스트는 1대로 확정하고 모든 평가를 같은 호스트에서 | 범위 | 계승 (호스트 자체는 G1 대기) |
| D24 | core는 DB unique 논리 키, 같은 키·다른 본문 409는 H02 | 기반 | 대체됨 → D35 |
| D25 | 결과 불명(UNKNOWN)은 core에서 사람이 실행하는 reconcile, 자동 재조회는 H04. 재실행 금지는 core | 기반 | 계승 |
| D26 | core 관찰은 로그 스트림 연속 읽기 + container·image 불변, heartbeat·cursor는 H03 | 배포 | 계승 |
| D27 | 신청서 문안 길이 조정, 내부 코드명 제거. 구현과 다르면 문장 삭제 | 범위 | 계승 |

### 2.3 v4 결정 (D28~D41) — spec 14 §7

| ID | 결정 한 줄 | 영역 | 상태 |
|---|---|---|---|
| D28 | 로그·Issue 두 입력을 하나의 work item 경로로 통합 | 입력 | 유효 |
| D29 | 안정 fingerprint와 routing_scope 분리 | 입력 | 유효 |
| D30 | 확정 binding 우선, 유사도는 후보로만, 조회 불완전 시 정지 | 입력 | 유효 |
| D31 | 한 repo outbound polling이 core, webhook은 P1 | 입력 | 유효 |
| D32 | Issue 처리 조건: 승인 actor·명시 scope·기존 작업 보호 | 보안 | 유효 |
| D33 | 시작 알림 receipt 전에는 수정 금지(host에서 강제) | 알림 | 유효 |
| D34 | durable notification outbox와 UNKNOWN 상태 | 알림 | 유효 |
| D35 | H01(claim 경합)·H02(같은 멱등키·다른 본문 409)를 core로 승격 | 기반 | 유효 (D24 대체) |
| D36 | case outcome을 실제 verifier·phase·origin으로 분리 | 기억 | 유효 |
| D37 | SQLite lexical 검색(FTS5)이 core. 별도 vector 인프라 없음 | 기억 | 유효 |
| D38 | cold_start / memory_assisted snapshot 분리 평가 | 기억 | 유효 |
| D39 | 성공 알림과 복구 판정 분리 | 알림 | 유효 |
| D40 | wire schema `linemedic.v4`, 무중단 호환 가정 제거 | 기반 | 유효 |
| D41 | 성공 확률 대신 남은 시간·증거 기반 체크포인트 | 범위 | 유효 |

### 2.4 구현 결정 (D42~) — DECISIONS.md §1·§3

| ID | 결정 한 줄 | 영역 | 상태 |
|---|---|---|---|
| D42 | `lineMedic/` 단일 git 저장소. Control Plane `linemedic/`, 데모 repo 시드 `l3-mes-api-seed/` | 기반 | 기본값 (저장소 이름 표기는 D63 반영) |
| D43 | Python 3.12 이상, `pyproject.toml` + venv, `requirements.lock` | 기반 | 기본값 |
| D44 | FastAPI + uvicorn, pydantic v2 strict·extra forbid, 원시 body 크기·중복 key 검사 후 모델 검증 | 기반 | 기본값 |
| D45 | stdlib `sqlite3`(ORM 없음), WAL·`BEGIN IMMEDIATE`, 번호 붙은 SQL migration | 기반 | 기본값 |
| D46 | GitHub REST는 `httpx` 직접 호출, `GitHubPort` + `FakeGitHub`, 봇·setup credential 분리 | 배포 | 기본값 (API 버전 헤더는 N11 대기) |
| D47 | Docker는 `docker` CLI를 argv 리스트로 호출, `DockerPort` + fake | 보안 | 기본값 |
| D48 | 운영 CLI `python -m linemedic.cli`(argparse), demo 전용 명령은 공개 endpoint 없음 | 기반 | 기본값 |
| D49 | 대시보드는 별도 프로세스, `127.0.0.1` bind, SQLite 읽기 전용, 쓰기 route 없음 | 보안 | 기본값 |
| D50 | run_id `r-YYYYMMDD-HHMMSS-xxxx`, 엔티티 ID는 접두사 + 대문자 16진 12자 | 기반 | 기본값 |
| D51 | 주입 가능한 `Clock`, 시각 저장은 마이크로초 UTC `Z` | 기반 | 기본값 |
| D52 | 비밀 아닌 설정은 config 파일(원래 `config/linemedic.yaml`), 비밀은 env, config hash를 run manifest에 기록 | 기반 | 기본값 (파일 형식은 D60이 대체) |
| D53 | runtime 에이전트는 `AgentAdapter` 뒤에. G4 전에는 `ScriptedAdapter`(origin=`manual_integration`)만 | 범위 | 기본값 (실제 adapter는 G4 대기) |
| D54 | 사례 검색 엔진 `sqlite_fts5` 또는 `keyword_fallback`, 토큰 인용·파라미터 바인딩 | 기억 | 기본값 |
| D55 | 테스트 `unit`/`integration`/`live` 분리와 pytest 마커, `make test`는 마커 테스트 제외 | 기반 | 기본값 |
| D56 | 린트·포맷은 ruff 하나 | 기반 | 기본값 |
| D57 | MES 로그는 stdout JSON Lines, detector와 verifier가 같은 스트림 사용 | 입력 | 기본값 |
| D58 | 정비 초안 안내 문구는 승인된 매뉴얼 템플릿에서 브로커가 채움 | 범위 | 기본값 (파일 형식은 D60이 대체) |
| D59 | S2-lite 사건은 `vision-inspection` 서비스(코드 경로 없음), 배포 기록은 `audit_events(DEPLOY_OBSERVED)` | 입력 | 기본값 |
| D60 | 설정·정책·계약·평가 기대값 파일은 TOML(`tomllib`), 모르는 키 거부, null은 키 생략, `pyyaml` 제거. OpenShell 정책만 YAML | 기반 | 유효 (D52·D58의 파일 형식 대체) |
| D61 | 진행 상태 원본은 `flow/state.toml`, STATUS.md는 생성 파일, `flowctl check`로 선행·게이트·증거·비밀 패턴 검사 | 범위 | 대체됨 → D62 |
| D62 | 개발 진행 상태·완료 보고는 STATUS.md에 직접 기록. 선행·게이트는 작업 카드로 확인하며 별도 진행 도구 제거 | 범위 | 유효 (D61 대체) |
| D63 | 저장소·문서 루트 이름은 `lineMedic`. GitHub 생성 목표는 조직 `lineMedic`의 공개 저장소 `lineMedic/lineMedic` | 기반 | 로컬 이름 유효; GitHub 게시 대상은 D64로 대체 |
| D64 | GitHub 게시 대상은 사용자 지정 `lineMedic/medicAgent`. 로컬 루트·제품·패키지·데모 대상 이름은 유지 | 기반 | 유효 (D63의 GitHub 게시 대상 대체) |
| D65 | `config_hash`는 설정 파일 + 설정 키에 대응하는 env만 포함. 호스트·run 식별 env는 `RuntimeEnv`로 분리해 해시에서 제외 | 기반 | 유효 (D52 구체화) |
| D66 | MES 로그에 `top_frame_line` 추가(`top_frame`은 줄 번호 없음), 로트 파일 형식 `{lot_id, records}` | 입력 | 유효 (D57 확장) |
| D67 | verifier reason 코드·판정 순서, 신뢰 prober 컨테이너로 internal network의 MES 호출, S1b는 MES 태그 위에 한 파일만 덮음 | 배포 | 유효 (spec 08 §5 구체화) |
| D68 | 제어 DB `<RUNS_DIR>/linemedic.db`, DDL 원문 migration + `schema_migrations`, 요청마다 `BEGIN IMMEDIATE`, SQLITE_BUSY 3회 재시도 후 정지, 전이 주체는 `Actor` 열거형 | 기반 | 유효 (D45 구체화) |
| D69 | 전 경로 Bearer 인증과 라우팅 전 prefix 가드, token hash 메모리 등록부, 범위 밖·없음 동일 404, `INVALID_REQUEST`·`INTERNAL_ERROR`, 성공 응답만 멱등 저장 | 보안 | 유효 (spec 03 §1·07 §3 구체화) |
| D70 | escalate body, operator는 PR_OPENED에서만 중단, 결합 전이의 WORK_BLOCKED/RECOVERY_NOT_VERIFIED intent를 outbox `enqueue`로 같은 트랜잭션에 기록(발송은 W26) | 알림 | 유효 (spec 03 §5 구체화) |
| D71 | `persist_result`로 결과 저장·verifier 전용 전이·RECOVERY_* intent를 한 트랜잭션에, 판정 중 오류는 INCONCLUSIVE, S1b 시험 사건은 demo 도우미로 준비하고 활성 run에 기록 | 배포 | 유효 (spec 08 §7·§9 구체화) |
| D72 | fingerprint 배열 인코딩·source 기준 service, 관찰 시계 60초 3회, terminal 사건 흡수, 정제 로그 JSONL 보관, `/tools`는 RUNNING attempt만, search_logs 제한 | 입력 | 유효 (spec 15 §3.1·03 §2 구체화) |
| D73 | S2-lite: vision-inspection·설비 catalog, 합성 지표 30분·60 sample, 연속 3 sample 이상 규칙, 설비 범위 조회 도구, 같은 라인 배포 기록·deployed_at | 입력 | 유효 (spec 09 §3·03 §2 구체화) |
| D74 | 제안 접수: B01·B02 동기, 멱등 재전송 우선, B02 422도 제출 합산 2회에 포함, 백그라운드 B03~B06(docs/03 §5 코드만), 거절 후 수정 1회·예산 소진 이관, create_pr는 W10 전까지 PROTECTION_UNAVAILABLE, 초안·blocker report payload, JSON Schema 생성 | 배포 | 유효 (spec 03 §3·§4·06 §1·§7 구체화) |
| D75 | GitHub 포트: 공용 경로·검증·쓰기 차단 base, HTTP 상태·전송 예외 매핑(`request_sent`), `write_enabled` 명시 필수 shadow, repo·route·작성자 catalog, doctor 봇 identity, live smoke 쓰기 이중 허락 | 배포 | 유효 (spec 15 §2·16 §1·02 §4 구체화, D46 확장) |
| D76 | Issue polling: scope별 활성화·checkpoint, 서버 시각 경계·cap delta는 읽은 곳까지, 처음 볼 때만 새 Issue 판정, 사람 작업·closed·권한 회수·삭제 처리, ETag·전체 조회·rate limit backoff, shadow planned, sync API | 입력 | 유효 (spec 15 §2·§3.3·§5 구체화) |
| D77 | 로그 → Issue: 갱신 후 lookup 1~5(form 형식·토큰 2개 후보·다른 scope 봇 Issue 제외), closed 연결은 운영자, attached(작업 복제 없음), 생성 intent·결과 불명·rate limit 재시도·거절 매핑, marker 템플릿, 조정 규칙, 운영자 연결 API·control_api 주소 | 입력 | 유효 (spec 15 §3·§4 구체화) |
| D78 | work lifecycle: API 트랜잭션에 멱등 기록 포함, 현재 snapshot 승인·자동 승인 정책, incident 연결 기록, scope 변경 상태별 처리, start_attempt 유일 attempt 발급·슬롯 대기, retry 중복 거절·새 incident, cancel 상태별, work 조회 API·CLI | 입력 | 유효 (spec 04 §3·§7·§8, spec 15 §6·§8 구체화) |
| D79 | 알림: worker 분리·SENDING 선커밋, 대상은 catalog·DB work만(no_bound_issue 미전송), shadow는 PENDING 유지, 안전한 거절만 3회 backoff(Retry-After 우선), UNKNOWN 재발송 금지·조정 규칙(봇+marker+본문 hash), provider·저장 시각 분리, 시작 게이트·60초 만료·늦은 receipt 무부활, 템플릿 정제, 상태 표시, 알림 운영 API·CLI | 알림 | 유효 (spec 16 §1~§7 구체화) |
| D80 | 패치 게이트: 정책 파일 단일 원본·segment glob, 좁은 diff 문법·거부 규칙, 기준 base 3자 일치, 작업 트리 없는 candidate·적용 뒤 tree 재확인·결정적 commit, runner 고정 image ID·실행 프로필·inspect 재확인·결과 mount 안전 읽기, R0/R1/R2 세부 판정, 게이트는 트랜잭션 밖·고칠 수 없는 결과는 즉시 멈춤·W11 전 통과는 멈춤, 에이전트 보기 축소, runner-image·doctor | PR·릴리스·검증 | 유효 (spec 06 §2~§5 구체화) |
| D81 | 봇 PR: 결정은 broker·실행은 PrOpener·세 트랜잭션, 생성 직전 재조회(scope·사람 작업·baseline·브랜치 점유), 재사용 기준, git push(force·hook·credential 노출 없음, porcelain 분류), 결과별 전이·blocker·side effect, 본문 템플릿(R1 실패 요약 포함)·에이전트 문장의 Issue 참조·URL 무력화와 closing keyword 검사·marker, 조정 규칙(무변경=PR·브랜치 없음), 실행 조회·조정 API·CLI 키 | PR·릴리스·검증 | 유효 (spec 06 §6·§8·§10, spec 15 §7 구체화) |
| D82 | exact SHA 배포: 사전 검사 순서·코드(논리 키 우선, merged·merge SHA·head·사람 리뷰·tree·image), 최신 변경 승인 기록, INTENDED·run lock, merge commit만 fetch·mirror tree 재확인·R0~R2 재실행·신뢰 레시피·image ID 기동·inspect, local image ID, 실패별 이관·환경 확인·복원 절차, UNKNOWN·DEPLOY 조정·재시작, releases API·approve-release 체크리스트 | PR·릴리스·검증 | 유효 (spec 08 §1~§3·§9, spec 11 §5 구체화) |
| D83 | 사람 제안 통합: adapter 계약(credential 키워드)·ScriptedAdapter, 게이트 뒤 workspace·context·token, adapter thread·deadline 강제·결과별 attempt 종료·만료·재시작 이관, attempt origin(PR 본문·배포 검증), make start 조립(기능별 끄기)·루프·기동 복구·신호·pid 파일·make stop, 시작 알림 60초를 발송 전·receipt 저장·만료 검사에서 시간으로 강제, 배포 lock 중 장애 주입 거부 | 입력 | 유효 (spec 05 §1·§10, spec 11 §4 구체화) |
| D84 | 사례 기억: 원본 event 5종·키, VERIFIED_SUCCESS 도메인 검사(PASS·관찰·image·contract·DEPLOY·merge SHA), series·revision·정제 실패 DRAFT·철회, FTS5 색인 파생물(재구축 DROP·재생성, 없으면 keyword_fallback), snapshot manifest(series 최신 PUBLISHED·제외 규칙·내용 hash ID·덮어쓰기 없음), 필터 후 top_k·outcome 묶음 병합·관련 실패 포함·UNAVAILABLE 구분·적용 조건 경고, history projection 재사용, search_cases·rebuild-index·case 조회 | 사례 기억·검색 | 유효 (spec 17, spec 04 §5.1·§6 구체화) |
| D85 | 대시보드: 화면·/ops/dashboard 공용 읽기 모델, 값 출처(run manifest·DB), 미확인/N/A 기준, docs/11 §5 매핑·표 밖 상태 문구·금지 표현 대조, 타임라인 9단계, 알림은 상태만, 127.0.0.1·mode=ro·query_only·GET만·autoescape·script 차단 CSP, INV-01 표시 전용 예외(SELECT 전용 확인) | 보안 | 유효 (spec 13 §1·§2, spec 07 §3, D49 구체화) |
| D86 | run 수명 주기: manifest identity·memory·baseline, 기준 브랜치(CREATE_BASELINE·G2·G10, repo ID 확인·이동 없음·실패 시 run 없음), 정지=active=0·루프 반복마다 확인, supervisor·broker·outbox run 범위, archive(정지→미해결→export→정리), export(private 원본·shared 정제·run-record·hash), 라벨·정확한 ID·한 경로만 정리(prune 없음), reset은 안내, /ops/runs·archive | 기반 | 유효 (spec 11 §3·§7·§9, spec 04 §9 구체화) |
| D87 | 에이전트 local 준비: 규칙 묶음(system·tools·skills) 읽기 전용·prompt hash, workspace work/repo·output·agent_rules, 시작 전 금지 자료 검사, 서버 측 도구 예산(TOOL_CALL·429, get_proposal 제외), tools client(ID 검사·재시도 없음), attempt trace(서버·로컬 도구 분리, token observed/partial/null), 모델 일시 오류 1회 재시도, agent_mode | 입력 | 유효 (spec 05 §1·§3·§5~§7 구체화) |
| D88 | sandbox 모드 준비: SandboxPort(prepare·close, 설정 없음·fake만), 준비 실패 시 local로 바꾸지 않고 시작 안 함, SANDBOX_PREPARED·CLOSED, 정책 파일 hash·effective policy(가림·내용 hash 이름)·필수 보호 10개, host가 정하는 sandbox_verified, attempt 전후 규칙 hash(AGENT_RULES_CHANGED), manifest·run-record·대시보드·doctor openshell | 보안 | 유효 (spec 05 §1·§3, spec 07 §4 구체화) |
| D89 | 코드 경로 없는 서비스(`code_paths = []`)의 create_pr은 패치 게이트 전에 PATCH_PATH_DENIED(수정 기회 유지, 제안 원본 보존), S2-lite fake E2E(기본·recent-deploy, 사람 설비 제안 → 초안 not_sent·HANDED_OFF·HANDOFF_DRAFTED·case HANDOFF, PR·빌드·배포 0건) | PR·릴리스·검증 | 유효 (spec 09 §3, AC-S2, D59·D73 구체화) |
| D90 | attempt 문맥 통합: workspace 전 host 초기 사례 검색(requested_by supervisor, 예산 밖) → context memory·trace, UNAVAILABLE 정책 `memory.on_unavailable`(기본 stop → LOOKUP_INCOMPLETE), get_incident memory, get_bound_issue(묶인 Issue만·정제한 비신뢰 본문·예산 안), T-MEM-06(로그 path 주입) | 사례 기억·검색 | 유효 (spec 05 §10·§11, spec 17 §5·§6 구체화) |
| D91 | S3 보안 시험(게이트 없는 부분): S3-B 결정론 표 14행·evidence, 공격 memo(고정 확인 코드·mock sink만·시험 표시 없음), mock sink(본문 없이 hash·확인 코드 여부), S3-C 판정 순서(도달 → 대조 실패 → 정책 거절 기록 ±5초), 같은 요청의 호스트 대조, security-test(sandbox 전 INCONCLUSIVE), sentinel 쓰기 프로브 | 보안 | 유효 (spec 07 §6 구체화) |
| D92 | 평가 하네스: strict 기대값 TOML(에이전트 비노출), preflight 없으면 run 없이 NOT_CONFIGURED, 새 평가 run(manifest evaluation)·start 분리 실행·주입 뒤 사람 단계에서 멈춤(승인 대행 없음), 사건마다 결과 행(사건 없음 포함), 조건 집합별 분자/분모·origin·NOT_RUN·거짓 완료·금지 행동 미확인 집계 | 범위·일정·평가 | 유효 (spec 09 §7~§9·§12 구체화) |
| D93 | 문서 정합성 자동 점검: docs/09 시험 파일·ID(줄임 펼침, hardening 행만 예외), STATUS LIVE_VERIFIED 행의 evidence 실재, README·제출 초안의 docs/11 §4 금지 문구(목록 동기화) | 범위·일정·평가 | 유효 (docs/11 §1·§4 구체화) |

## 3. 주제별 보기

| 영역 | 결정 |
|---|---|
| 범위·일정·평가 | D01 D02 D13 D15 D16 D18 D19 D23 D27 D41 D53 D58 D61 D62 D92 D93 |
| 입력(로그·Issue·work) | D08 D28 D29 D30 D31 D57 D59 D66 D72 D73 D76 D77 D78 D83 D87 |
| 알림 | D33 D34 D39 D70 D79 |
| 사례 기억·검색 | D36 D37 D38 D54 D84 D90 |
| 보안·권한·격리 | D09 D10 D14 D20 D22 D32 D47 D49 D69 D85 D88 D91 |
| PR·릴리스·검증 | D03 D04 D05 D06 D07 D12 D21 D26 D46 D67 D71 D74 D75 D80 D81 D82 D89 |
| 기반(저장소·스택·동시성) | D11 D17 D24 D25 D35 D40 D42 D43 D44 D45 D48 D50 D51 D52 D55 D56 D60 D63 D64 D65 D68 D86 |

## 4. 게이트 대기 결정

[DECISIONS.md §2](DECISIONS.md)의 미정 항목이다. 확정되면 D65 이후 번호로 DECISIONS.md에 기록하고 여기서 행을 옮긴다.

| 항목 | 게이트·스파이크 | 확정 전 처리 |
|---|---|---|
| 데모 호스트(OS·arch) | G1 | 미정. 평가 run은 확정 호스트에서만 |
| agent runtime (OpenClaw/NemoClaw vs NAT) | G4 (N01·N02) | `ScriptedAdapter`까지만 구현 |
| NVIDIA 모델 ID·endpoint | G3 (N01) | 후보명만 있음. 확인 전 코드에 고정하지 않음 |
| OpenShell 정책 schema·버전 | G5 (N03·N04·N09) | 미확인 YAML을 실행 설정으로 쓰지 않음 |
| GitHub API 버전 헤더 | N11 (G2 후) | config 키만 두고 값은 확인 후 기입 |
| 알림 채널 | G12 | `github_comment` 1개. SMTP는 선택 시에만 |
| NeMo Guardrails 사용 여부 | G6 (R2 답변) | 사용하지 않음. 필수로 확인되면 새 결정 |

## 5. 새 결정을 추가하는 절차

1. 카드 범위 밖의 공개 계약(API 필드·enum·DDL·상태 전이·검증 조건)이나 [docs/01](docs/01-tech-stack.md)에 없는 의존성이 필요하면 **구현 전에** 결정을 기록한다([AGENTS §3·§4.4](AGENTS.md)).
2. [DECISIONS.md §3](DECISIONS.md)에 다음 번호로 한 행을 추가한다. 형식은 `결정 / 이유 / 영향 문서 / 재시험`이다([spec 14 §6](spec/docs/14-decisions-sources.md)).
3. 같은 변경에서 이 파일 §2.4에 한 줄, §3에 ID를 추가한다.
4. 기존 결정을 바꿀 때는 원래 행을 지우지 않는다. 새 행에 "Dxx를 대체"라고 적고, 이 색인에서 옛 행의 상태를 `대체됨 → Dyy`로 바꾼다.
5. 공개 계약을 바꾸면 docs·테스트·관련 카드를 같은 커밋에서 고친다. spec과 docs/tasks가 충돌해 docs/tasks를 고쳤다면 그 사실도 DECISIONS.md에 한 줄 남긴다([AGENTS §8](AGENTS.md)).
6. spec/은 수정하지 않는다. spec의 결정을 뒤집어야 하면 DECISIONS.md에 기록한다. 기록된 항목은 spec보다 우선한다.

### 행 템플릿 (DECISIONS.md §3)

```markdown
| D65 | <결정 한 문장. 대체하면 "D24를 대체"> | <이유·대가> | <영향 문서: docs/NN, tasks/Wxx> | <재시험: 테스트 ID 또는 명령> |
```

### 색인 행 템플릿 (이 파일 §2.4)

```markdown
| D65 | <결정 한 줄> | <영역> | 유효 |
```
