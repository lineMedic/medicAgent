# STATUS — LineMedic v4 구현 진행표

> **개발 진행 상태와 완료 보고의 유일한 원본 — 직접 편집한다.** 착수·완료·차단·재개 때 아래 표와 완료 보고를 함께 갱신한다([D62](DECISIONS.md)). 작업 범위·선행·게이트·수용 기준은 [tasks/](tasks/README.md) 카드에서 확인한다.
> 상태 enum과 증거 규칙은 [docs/11](docs/11-definition-of-done.md)에 있다. 시각은 UTC로 적는다(예: `2026-09-27T05:10Z`). 상태를 올리기 전에 선행·해당 실행의 사람 승인·원본 증거를 직접 확인한다. 비밀값·수신 주소를 기록하지 않는다.
> 초기값은 원본 [spec 10 §2](spec/docs/10-delivery-plan.md)과 같이 전부 `NOT_CHECKED`다. 이 문서 패키지를 작성한 것만으로는 어떤 W도 완료되지 않았다.

## 현재 작업

| 카드 | 시작 시각 | 진행 메모 |
|---|---|---|
| B00 | 2026-09-27T02:10Z | 사람 게이트 G1·G2·G3·G5·G6·G11 요청 기록 후 착수. 작업 위치: fork `minjcho/medicAgent` clone (upstream `lineMedic/medicAgent`) |

## 다음 작업

[AGENTS.md §2](AGENTS.md)의 선택 조건에 따른 다음 카드: **B00** ([tasks/B00-bootstrap.md](tasks/B00-bootstrap.md)). 구현을 시작할 때 먼저 [docs/08 §2](docs/08-task-plan.md)의 게이트 요청을 사용자에게 전달한다.

## 작업표

| # | 카드 | 목표 상태 | 현재 상태 | 증거 (명령·커밋·경로) | 게이트·차단 | 갱신 |
|---|---|---|---|---|---|---|
| 1 | B00 | UNIT_TESTED | NOT_CHECKED | | | |
| 2 | W00 | LIVE_VERIFIED | NOT_CHECKED | | G1 | |
| 3 | W01 | LIVE_VERIFIED | NOT_CHECKED | | G6 | |
| 4 | W02 | LIVE_VERIFIED | NOT_CHECKED | | G3·G4·G5 | |
| 5 | W03 | LIVE_VERIFIED | NOT_CHECKED | | G2 | |
| 6 | W04 | UNIT_TESTED | NOT_CHECKED | | | |
| 7 | W05 (1부) | UNIT_TESTED | NOT_CHECKED | | | |
| 8 | W06 | UNIT_TESTED | NOT_CHECKED | | | |
| 9 | W05 (2부) | UNIT_TESTED | NOT_CHECKED | | | |
| 10 | W07 | UNIT_TESTED | NOT_CHECKED | | | |
| 11 | W08 | UNIT_TESTED | NOT_CHECKED | | | |
| 12 | W09 | UNIT_TESTED | NOT_CHECKED | | | |
| 13 | W22 | LIVE_VERIFIED | NOT_CHECKED | | G2·G10 | |
| 14 | W23 | LIVE_VERIFIED | NOT_CHECKED | | G2 | |
| 15 | W24 | LIVE_VERIFIED | NOT_CHECKED | | G2·G10 | |
| 16 | W25 | UNIT_TESTED | NOT_CHECKED | | | |
| 17 | W26 | LIVE_VERIFIED | NOT_CHECKED | | G2·G10 (G12 선택) | |
| 18 | W10 | UNIT_TESTED | NOT_CHECKED | | | |
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
| R1 Skill API | UNCONFIRMED | |
| R2 NeMo Framework/Microservices | UNCONFIRMED | |
| R3 심사 항목 | PROVISIONAL (제공 자료 기준, 최종 원문 확인 필요) | |
| R4 데모·코드·개별 신청 | UNCONFIRMED | |
| R5 공식 마감 | UNCONFIRMED (원안의 더 이른 일정을 보수적으로 사용) | |

## 완료 보고 기록

카드를 끝내거나 멈출 때마다 [docs/11 §3](docs/11-definition-of-done.md) 양식으로 이 절에 직접 추가한다(최신이 위). 카드 밖의 문서 변경은 제품 카드 완료와 구분해 기록한다.

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
