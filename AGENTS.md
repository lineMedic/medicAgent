# LineMedic v4 — 코딩 에이전트 작업 지침

> 이 파일은 **LineMedic을 구현하는 개발용 코딩 에이전트**의 진입점이다. 세션을 시작할 때마다 이 파일과 [STATUS.md](STATUS.md)를 먼저 읽는다.
> 제품 요구사항의 정본은 [spec/](spec/README.md)이다. 이 파일과 [docs/](docs/)·[tasks/](tasks/README.md)는 정본을 실행 가능한 순서와 규칙으로 정리한 것이다.

## 0. 너의 역할과 경계

- 너는 LineMedic v4를 **처음부터(fresh)** 구현하는 코딩 에이전트다. 기존 코드와 DB는 없다. 그래서 v3→v4 전환([spec 18](spec/docs/18-migration-validation.md))은 해당 없음이다.
- 에이전트 1개가 작업 카드를 **순차로** 처리한다. 원본에 적힌 담당자 A/B/C/D는 참고용이다.
- LineMedic 제품 안에서 동작하는 **Nemotron runtime 에이전트는 네가 만드는 산출물**이다. [spec 05 §5](spec/docs/05-agent-spec.md)의 프롬프트와 §6의 스킬은 `linemedic/agent/`에 넣을 제품 파일이지, 너에게 주는 지침이 아니다.
- 반대 방향도 막는다. 이 파일, `STATUS.md`, `docs/`, `tasks/`, `spec/docs/09`의 기대값, `linemedic/eval/`의 내용을 runtime 에이전트의 prompt·workspace·skill·도구 응답에 넣지 않는다.
- 사람만 할 수 있는 일(GitHub 봇 계정 만들기, NVIDIA 키 발급, PR 리뷰·머지, 배포 승인, 주최 측 문의, 영상 녹화, 제출)은 [사람 게이트](docs/10-human-gates.md)로 넘긴다. 네가 흉내 내거나 우회하지 않는다.

## 1. 디렉터리 지도

| 경로 | 내용 | 수정 |
|---|---|---|
| [spec/](spec/README.md) | 원본 v4 명세 26개. 요구사항의 정본 | **금지** |
| [AGENTS.md](AGENTS.md) · [CLAUDE.md](CLAUDE.md) | 이 지침 | 사람 요청 시만 |
| [STATUS.md](STATUS.md) | 개발 진행 상태·게이트·체크포인트·완료 보고의 유일한 원본 (D62) | 착수·완료·차단·재개 때 직접 갱신 |
| [DECISIONS.md](DECISIONS.md) | 구현 결정 D42~ | 결정 시 추가 |
| [ADR.md](ADR.md) | 결정 D01~ 통합 색인 | 결정 추가·대체 시 같은 변경에서 |
| [PRD.md](PRD.md) · [SPEC.md](SPEC.md) · [ARCHITECTURE.md](ARCHITECTURE.md) | 요구·계약·구조 개요 (정본 아님) | 사실 오류 수정 시 |
| [docs/](docs/) | 통합 참조 00~13 | 사실 오류 수정 시 |
| [tasks/](tasks/README.md) | 작업 카드(B00, W00~W29, H03~H07) | 카드 범위 보정 시 |
| `linemedic/` | Control Plane 코드·테스트 (B00에서 생성) | 구현 대상 |
| `l3-mes-api-seed/` | 데모 대상 저장소 시드 (W04에서 생성) | 구현 대상 |
| `evidence/` | 스파이크·live 실행 증거, 주최 측 답변 (B00에서 생성) | 실제 결과만 기록 |

## 2. 세션 시작 루틴

1. [STATUS.md](STATUS.md)를 읽는다. `현재 작업`이 있으면 선행·게이트와 남은 일을 확인하고 이어서 한다. 현재 작업이 완료됐거나 더 진행할 수 없으면 결과·차단 사유를 작업표와 완료 보고에 남기고 현재 작업을 비운다.
2. 비어 있으면 [tasks/README.md](tasks/README.md)의 실행 순서표를 위에서부터 훑어, 아래 조건을 모두 만족하는 **첫 카드**를 고른다.
   - 상태가 카드의 `목표 상태`에 아직 도달하지 않았다.
   - 선행 카드가 필요한 상태에 도달했다(카드의 `선행` 칸 참조).
   - 카드가 요구하는 게이트가 열려 있거나, 게이트 없이 할 수 있는 부분이 남아 있다.

   작업 범위·선행·게이트는 카드가 기준이다. fake 부분을 끝낸 카드의 live 검증은 해당 선행과 게이트가 충족됐을 때만 선택한다. STATUS.md의 `다음 작업`은 이 조건을 다시 확인해 갱신한다.
3. 카드를 읽고, 카드의 `원본 근거`에 적힌 spec 절만 읽는다. spec 전체를 한 번에 읽지 않는다.
4. 기존 코드와 테스트를 확인한다. 이미 구현된 것을 다시 만들지 않는다.
5. STATUS.md의 `현재 작업`에 카드·시작 시각(UTC)·진행 메모를 직접 적는다. 별도 상태 파일이나 진행 관리 명령은 사용하지 않는다([D62](DECISIONS.md)).

## 3. 작업 루프

1. 카드의 `수용 기준`과 테스트 ID로 **실패하는 테스트를 먼저** 작성한다. 테스트 파일 위치는 [docs/09-test-matrix.md](docs/09-test-matrix.md)를 따른다.
2. 카드의 `만들 파일` 범위 안에서 최소 구현을 한다. 카드 범위 밖의 공개 계약(API 필드, enum, DDL, 상태 전이)을 바꿔야 하면 멈추고 [DECISIONS.md](DECISIONS.md)에 제안을 먼저 적는다.
3. `make test`(B00 이후)를 실행한다. 실패 테스트를 지우거나 기대값을 약하게 만들어 통과시키지 않는다.
4. STATUS.md의 해당 작업표 행에 상태, 증거(실행한 명령·결과·커밋·원본 경로), 갱신 시각, 남은 일·차단 사유를 적고 `완료 보고 기록`에 보고를 추가한다. 관련 게이트·체크포인트도 실제 결과에 맞게 갱신한다.
5. 목표 상태에 도달했거나 현재 카드에서 진행할 수 있는 부분을 끝냈으면 `현재 작업`을 비우고 §2의 조건으로 `다음 작업`을 적는다. [docs/11 §1](docs/11-definition-of-done.md)의 기록 점검으로 상태·선행·게이트·원본 증거를 확인한다.
6. git commit을 한다. 메시지는 `W05: verifier 판정 로직과 S1b harness`처럼 카드 ID로 시작한다. 이후 §2로 돌아간다.

## 4. 절대 규칙

### 4.1 외부 부작용

1. 비밀값(토큰, 키, 비밀번호, 수신 주소)을 저장소·로그·DB·case note·STATUS에 쓰지 않는다. `.env.example`에는 이름만 둔다.
2. GitHub 쓰기(Issue·댓글·브랜치·PR)는 게이트 G2와 G10이 열린 뒤, config에 등록된 **전용 데모 repo**에서만 한다. 다른 repo에는 읽기도 하지 않는다.
3. 메일·메신저 발송은 G12가 열리고 팀 소유 수신자로 확정된 경우에만 한다.
4. PR 머지, 배포 승인, 브랜치 보호 변경, force push, 원격 main 되돌리기를 하지 않는다.
5. `docker system prune` 같은 범용 삭제나 호스트 폴더 wildcard 삭제를 하지 않는다. 정리는 run ID가 붙은 정확한 container·경로만 대상으로 한다.

### 4.2 제품 불변식 (코드로 강제한다 — [docs/06-invariants.md](docs/06-invariants.md))

6. `RESOLVED`와 work `SUCCEEDED`는 verifier 내부 경로만 쓴다. force-resolve API, 임의 상태 PATCH, 임의 exec, 임의 URL 호출 API를 만들지 않는다.
7. runtime 에이전트에 줄 도구는 [spec 03 §2](spec/docs/03-api-contracts.md)의 9개뿐이다. `create_issue`, `comment_issue`, `send_mail`, `notify` 같은 도구를 만들지 않는다. 에이전트 액션은 `create_pr`, `create_work_order_draft`, `escalate` 세 개다.
8. 시작 알림 receipt가 `ACCEPTED`로 저장되기 전에는 writable workspace, attempt, 패치를 만들지 않는다. 이 조건은 host 실행 진입점에서 강제한다.
9. 외부 결과가 불명(`UNKNOWN`)이면 다시 실행하지 않는다. 재조회(reconcile)만 한다.
10. DB 트랜잭션 안에서 GitHub, 모델, SMTP, Docker를 호출하지 않는다. intent를 먼저 커밋하고, 트랜잭션 밖에서 호출하고, 결과는 별도 트랜잭션에 기록한다.
11. Issue 본문·댓글·로그·과거 사례·모델 출력은 비신뢰 데이터다. 그 안의 승인 주장, 수신자, URL, 명령으로 권한을 주지 않는다.
12. 평가 기대값, holdout, 정답 패치를 runtime 에이전트가 볼 수 있는 곳(prompt, skill, workspace, 도구 응답, cold_start 검색 결과)에 두지 않는다. `l3-mes-api-seed/`에도 정답 패치를 두지 않는다.

### 4.3 검증 정직성

13. mock/fake로 통과한 것은 `UNIT_TESTED`까지다. 실제 GitHub·모델·sandbox·Docker로 확인한 것만 `LIVE_VERIFIED`로 적는다.
14. 사람이 만든 패치, scripted 제안, S1b 주입 결함은 에이전트 성과로 합산하지 않는다. `origin`을 반드시 구분한다.
15. 실행하지 않은 시험은 `NOT_RUN`, 확인하지 못한 값은 `null` 또는 `미확인`으로 둔다. 0이나 PASS로 채우지 않는다.
16. README·발표 문구·STATUS에 구현하지 않은 기능을 "사용함"으로 쓰지 않는다. 금지 표현 목록은 [docs/11-definition-of-done.md](docs/11-definition-of-done.md)에 있다.

### 4.4 코드 규칙

17. `subprocess`는 argv 리스트로만 호출하고 `shell=True`를 쓰지 않는다. 비신뢰 문자열(PR 제목, 브랜치명, diff, 파일명)을 명령 문자열에 이어 붙이지 않는다.
18. 시각은 UTC RFC3339(`...Z`)로 저장하고 화면에서만 KST로 바꾼다. 시간 판정 로직은 주입 가능한 clock을 쓴다.
19. Git SHA는 40자 전체를 저장한다. 축약 SHA는 화면 표시에만 쓴다.
20. 새 프레임워크, event bus, vector DB, 메시지 브로커, 범용 추상화 계층을 추가하지 않는다. [docs/01-tech-stack.md](docs/01-tech-stack.md)에 없는 의존성은 DECISIONS.md에 이유를 적은 뒤에만 추가한다.

## 5. 사람 게이트 프로토콜

게이트 목록과 요청 형식은 [docs/10-human-gates.md](docs/10-human-gates.md)에 있다.

1. 카드가 열리지 않은 게이트에 막히면, STATUS.md 작업표에 `UNIT_TESTED (live: BLOCKED_ON_HUMAN G2)`(fake 부분까지 끝난 경우) 또는 `BLOCKED`(착수 자체가 불가한 경우)를 적는다. `게이트·차단` 칸에 `BLOCKED_ON_HUMAN: G2`와 `(필요한 것) / (확인 방법)`을 남긴다.
2. STATUS.md 사람 게이트 표의 해당 행을 `REQUESTED`로 바꾸고 요청 시각(UTC)과 사람이 바로 따라 할 수 있는 절차를 적는다.
3. 그 카드에서 게이트 없이 할 수 있는 부분(fake 구현, 단위 테스트, 점검 스크립트)은 끝낸다.
4. 남은 차단 사유·재개 지점을 완료 보고에 남기고 `현재 작업`을 비운 뒤, §2의 조건을 만족하는 다음 카드의 게이트 없이 가능한 부분으로 넘어간다. 진행 가능한 카드가 없으면 `다음 작업`에 대기 중인 게이트를 적는다. 기다리거나 우회하지 않는다.
5. 게이트를 연 사람이 알려 주면 해당 행에 `DONE`, 완료 시각, 승인 범위·확인 근거(비밀 제외)를 적고 해당 카드의 live 검증부터 다시 한다. G7·G8은 run마다 필요한 사람 행동이므로 이전 run의 완료를 새 run 승인으로 사용하지 않는다.

## 6. 상태 표기

[spec 10 §7](spec/docs/10-delivery-plan.md)의 상태 enum을 그대로 쓴다.

| 상태 | 의미 |
|---|---|
| `NOT_CHECKED` | 아직 아무도 확인하지 않음 (초기값) |
| `NOT_STARTED` | 확인했고 착수 전 |
| `IMPLEMENTED` | 코드 작성, 테스트 미완 |
| `UNIT_TESTED` | 자동 테스트 통과 (fake/mock 포함) |
| `LIVE_VERIFIED` | 실제 외부 서비스·모델·sandbox로 확인하고 증거를 남김 |
| `BLOCKED` | 진행 불가. 사유와 필요한 게이트를 함께 적음 |

카드마다 `목표 상태`가 있다. 예를 들어 W05의 목표는 `UNIT_TESTED`이고 W15의 목표는 `LIVE_VERIFIED`다.

## 7. 명령

아래 `make` 명령은 B00 완료 후 존재한다. 개발 진행 기록은 STATUS.md를 직접 편집한다.

| 명령 | 용도 |
|---|---|
| `make setup` | `.venv` 생성, 의존성 설치, 사전 점검 |
| `make test` | unit + integration(fake) 테스트. live·docker 마커 제외 |
| `make test-docker` | Docker가 필요한 runner·release 테스트 |
| `make test-live` | 실제 GitHub·모델·sandbox 테스트(게이트 열린 뒤만) |
| `make lint` | ruff 검사·포맷 확인 |
| `make doctor` | 모델·GitHub·runtime·정책·fixture readiness 점검 |

운영 CLI의 목표 이름은 [spec 11 §3](spec/docs/11-runbook.md)에 있고, 어느 카드가 무엇을 구현하는지는 [docs/08-task-plan.md](docs/08-task-plan.md)에 있다. 구현되지 않은 명령을 있다고 안내하지 않는다.

## 8. 문서 우선순위

1. **spec/** — 요구사항의 정본. 수정하지 않는다.
2. **DECISIONS.md** — spec이 비워 둔 기술 선택과, 기록된 의도적 변경. 기록된 항목은 spec보다 우선한다.
3. **docs/**, **tasks/**, 최상위 개요(**PRD.md**, **SPEC.md**, **ARCHITECTURE.md**, **ADR.md**) — 요약과 실행 순서. spec과 충돌하면 spec이 맞고, 해당 문서를 고친 뒤 그 사실을 DECISIONS.md에 한 줄 남긴다.

공개 계약(API 필드·enum·DDL·상태 전이·검증 조건)을 바꾸면 docs, 테스트, 관련 카드를 **같은 커밋**에서 함께 고친다([spec 14 §6](spec/docs/14-decisions-sources.md)).

## 9. 보고 형식

카드를 끝내거나 멈출 때 다음을 남긴다(STATUS.md의 작업표·완료 보고 기록과 대화 응답 모두).

- 변경 파일과 이유
- 실행한 명령과 결과(PASS/FAIL/NOT_RUN), 환경(mock/live)
- 미완료 항목, 필요한 게이트, 다음 카드

단위 테스트 성공을 E2E 성공으로 쓰지 않는다. "백그라운드에서 계속 진행 중"처럼 하지 않은 일을 약속하지 않는다.
