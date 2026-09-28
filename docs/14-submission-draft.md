# 14. 제출 초안 — 신청서·영상 구성·제출 전 점검 (W21)

> 원본: [spec 13 §4·§5·§8](../spec/docs/13-demo-submission.md), [spec 12 §7](../spec/docs/12-nvidia-requirements.md), [tasks/W21](../tasks/W21-readme-submission.md).
> **에이전트가 쓴 초안이다.** 사람이 실제 실행 결과로 다시 확인하고 확정한다. 영상 녹화·신청서 입력·팀원 전원 개별 제출은 사람이 한다. 에이전트는 제출·게시하지 않는다.
> 기준 시점: 2026-09-28T11:10Z(KST 20:10). 이날 live 재현(1~6차)으로 S1 live run 2가 사람 제안으로 업무 검증 PASS까지 갔다. Nemotron 에이전트 제안·OpenShell sandbox는 없다([STATUS.md](../STATUS.md)). 팀이 쓰는 마감은 2026-09-28 23:59 KST(사용자 전달, 주최 측 답변 원문은 아님 — R5 UNCONFIRMED).

## 1. 신청서 초안

### Problem Definition (300자 내외)

> 공장 IT 장애 대응에서 로그, GitHub 이슈, 수정 이력이 분리돼 있으면 같은 오류를 중복 조사하거나 이미 진행 중인 작업과 충돌할 수 있습니다. AI가 수정안을 만들어도 실제 업무가 복구됐는지, 해결하지 못한 이유가 무엇인지 확인되지 않으면 현장에서 믿고 쓰기 어렵습니다. 과거 실패를 성공 사례처럼 재사용하지 않는 대응 기록도 필요합니다. LineMedic은 이 문제를 합성 MES·설비 시뮬레이터 환경에서 다룹니다.

### Solution (500자 내외)

> LineMedic은 MES 오류 로그와 등록된 GitHub Issue를 하나의 작업으로 연결합니다. 대응 Issue가 있으면 재사용하고, 없으면 만들며, 후보가 여럿이면 멈추고 운영자에게 넘깁니다. 작업 시작 댓글이 등록된 뒤에만 수정 시도를 시작합니다. 수정안은 증거 범위·패치 정책·격리 컨테이너 재현 검사를 통과해야 봇 PR이 되고, 사람이 리뷰·머지한 정확한 커밋만 배포 승인 뒤 빌드됩니다. 독립 업무 계약 검증을 통과해야 복구로 기록하고 결과를 Issue 댓글로 남깁니다. 이 경로를 실제 GitHub와 Docker로 끝까지 실행했습니다(이번 run의 수정안은 사람이 미리 작성). NVIDIA Nemotron은 도구 호출 형식 시험까지 확인했고, 에이전트가 수정안을 직접 만드는 연결과 OpenShell 격리는 다음 단계입니다.

### Tech Stack

| 구분 | 내용 |
|---|---|
| NVIDIA | Nemotron `nvidia/nemotron-3.5-lightning-30b-a3b` — NVIDIA API cloud endpoint(OpenAI 호환), 도구 호출 형식 시험(N01)에서 실제 호출. runtime(NemoClaw/NAT)·OpenShell은 미통합 |
| 언어·서버 | Python, FastAPI(Starlette), pydantic, uvicorn, httpx |
| 저장 | SQLite(WAL). 사례 검색은 SQLite FTS5 lexical 검색 |
| 격리·배포 | Docker 컨테이너(검사 runner·MES 배포 대상), image ID 고정 |
| GitHub | REST API: Issue·댓글·PR 생성, 60초 주기 polling. 합성 데모 repo `lineMedic/l3-mes-api` |
| 알림 | GitHub Issue 댓글 |

### 뺀 문장 (구현되지 않았거나 확인 전)

| spec 13 §5 문장 | 뺀 이유 | 다시 넣을 조건 |
|---|---|---|
| Nemotron 에이전트는 현재 로그·코드·설비 지표와 허용된 과거 사례를 조회해 코드 수정, 설비 점검 요청 초안, 진행 불가 보고를 구분합니다 | runtime adapter가 없다. 이번 run의 수정안은 사람이 미리 쓴 것이다 | G4 뒤 실제 모델 run(W14·W16) |
| 에이전트는 OpenShell 경계 안에서 동작하며 | OpenShell 정책·sandbox 구현이 없다 | G5 뒤 `sandbox_verified=true` run(W15) |

## 2. 영상 구성표 (3분, S1 live run 2 기록 기준)

모든 장면은 **run `r-20260928-102418-c75e`의 실제 화면·기록**이다. 녹화는 run 뒤 기록을 다시 보여 주는 방식이므로 "당시 실시간 화면"이라고 하지 않는다. 자막에 run ID와 "수정안은 사람이 미리 작성(manual_integration)"을 표시한다.

| 구간 | 장면 | 보여 줄 화면 |
|---|---|---|
| 0:00~0:20 | 문제: 불량 집계 API 500 | README 첫 단락, 합성 환경 설명. `make scenario-s1` 출력(500 ×3) |
| 0:20~0:45 | 로그 → Issue 자동 생성 | GitHub Issue #9(제목·본문·`linemedic` 라벨), 사건 ID |
| 0:45~1:05 | 시작 댓글이 먼저, 그다음 시도 | Issue #9의 시작 댓글 → 대시보드(`make dashboard RUN_ID=r-20260928-102418-c75e`)의 receipt·attempt 시각 |
| 1:05~1:30 | 검사한 봇 PR | PR #10: 1줄 수정 + 재현 테스트, 본문 `Related to #9`, 검사 13개 PASS(run 기록) |
| 1:30~1:55 | 사람 리뷰·머지와 배포 승인 | PR #10 Approve(`daejung-kim96`)·머지, `make approve-release` 체크리스트와 `approve` 입력 |
| 1:55~2:20 | 정확한 커밋만 배포 → 업무 검증 | run-record의 merge SHA → image ID → verification PASS, identity chain 표([evidence/W13-s1-live-run2.md](../evidence/W13-s1-live-run2.md)) |
| 2:20~2:35 | 결과 댓글과 안전 장치 | Issue #9의 복구 확인 댓글. S1b(잘못된 200) 거절, 후보가 둘이면 Issue를 만들지 않음(S4-ambiguous) |
| 2:35~3:00 | NVIDIA와 다음 단계 | N01 Nemotron 도구 호출 결과. "에이전트 수정안 생성·OpenShell 격리는 다음 단계" 자막 |

편집 규칙:

- 생략한 사람 승인·대기 시간은 자막으로 표시한다. 사람 없이 바로 끝난 것처럼 보이게 하지 않는다
- run 1(PR #4, 리뷰 없이 머지)과 run 2 장면을 섞지 않는다
- 공식 영상 길이(R4)가 이 구성표보다 우선한다

## 3. 제출 전 점검 결과 (2026-09-28, 이 초안과 README 기준)

| 점검 | 결과 | 근거·남은 일 |
|---|---|---|
| 모델 산출물과 사람 산출물이 구분돼 있다 | 충족 | run 2 수정안은 `manual_integration`이라고 README·초안·증거에 적었다. 모델 산출물은 N01 형식 시험뿐이다 |
| 실제 runtime·sandbox 통합 수준이 그림·문구·영상과 일치한다 | 충족(초안) | runtime·sandbox 미통합이라고 적었다. 영상 자막에도 넣는다 |
| Skill API·NeMo 조건(R1·R2)의 근거 또는 미확인 상태가 남아 있다 | 미확인 | [evidence/contest-conditions.md](../evidence/contest-conditions.md), G6 답변 없음 |
| cloud API 데모를 폐쇄망 실행이라고 쓰지 않았다 | 충족 | NVIDIA API cloud endpoint라고 적었다 |
| GPU·크레딧·상용 라이선스를 확인 없이 전제하지 않았다 | 충족 | 전제하는 문장이 없다 |
| 쓰지 않은 기술을 "사용"으로 적지 않았다 | 충족 | Nemotron은 N01 호출만, runtime·OpenShell은 미통합으로 적었다 |
| S1/S2-lite/S1b/S3/S4~S7 중 실제 완료·미실행을 구분했다 | 충족 | live: S1(run 2)·S1b·S4·S5-new. 평가 run은 없음(NOT_RUN) |
| Issue 확인·시작 receipt·attempt 시각·PR·검사 identity가 연결된다 | 충족 | [evidence/W13-s1-live-run2.md](../evidence/W13-s1-live-run2.md) |
| 댓글·메일 접수와 사람 수신·열람을 혼동하지 않았다 | 충족 | "댓글 등록"(접수)으로만 적는다 |
| case origin·snapshot·검색 엔진·cold/memory 분모를 명시했다 | 부분 | 검색 엔진(SQLite FTS5 lexical)은 적었다. S7 run 없음 |
| 사람 패치·승인·배포·S1b를 무인 agent 성과로 합산하지 않았다 | 충족 | run 2는 사람 제안·사람 승인·사람 배포 승인이며 agent 성과로 세지 않는다 |
| 회사·고객 데이터 없이 합성 환경임을 밝혔다 | 충족 | README 첫머리 |
| R1~R5 원문·답변 또는 미확인 상태를 보존했다 | 미확인 | G6 답변 없음. 마감은 사용자 전달 값(9/28 23:59 KST) |
| 영상 길이·저장소 공개/심사자 접근·팀원 개별 제출을 확정 조건(R4·R5)에 맞췄다 | 사람 확인 | 신청서 문항과 공개 범위는 사람이 제출 화면에서 확인한다 |
| docs/11 §4의 금지 표현이 없다 | 충족 | `make test`의 `unit/test_docs_consistency.py`가 README와 이 문서를 본다. §4 표현 24개 중 23개를 문자열로 찾고, '3/3 성공'은 그 자체가 아니라 거기서 끌어낸 결론 문구를 찾는다. 검사 목록은 §4 표와 양방향으로 대조한다 |
