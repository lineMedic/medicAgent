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

## 3. 주제별 보기

| 영역 | 결정 |
|---|---|
| 범위·일정·평가 | D01 D02 D13 D15 D16 D18 D19 D23 D27 D41 D53 D58 D61 D62 |
| 입력(로그·Issue·work) | D08 D28 D29 D30 D31 D57 D59 D66 D72 D73 |
| 알림 | D33 D34 D39 D70 |
| 사례 기억·검색 | D36 D37 D38 D54 |
| 보안·권한·격리 | D09 D10 D14 D20 D22 D32 D47 D49 D69 |
| PR·릴리스·검증 | D03 D04 D05 D06 D07 D12 D21 D26 D46 D67 D71 D74 |
| 기반(저장소·스택·동시성) | D11 D17 D24 D25 D35 D40 D42 D43 D44 D45 D48 D50 D51 D52 D55 D56 D60 D63 D64 D65 D68 |

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
