# 14. 제출 초안 — 신청서·영상 구성·제출 전 점검 (W21)

> 원본: [spec 13 §4·§5·§8](../spec/docs/13-demo-submission.md), [spec 12 §7](../spec/docs/12-nvidia-requirements.md), [tasks/W21](../tasks/W21-readme-submission.md).
> **에이전트가 쓴 초안이다.** 사람이 실제 실행 결과로 다시 확인하고 확정한다. 영상 녹화·신청서 입력·팀원 전원 개별 제출은 사람이 한다. 에이전트는 제출·게시하지 않는다.
> 기준 시점: 2026-09-28. 이때 live 검증은 없다(모든 `LIVE_VERIFIED` 목표가 게이트 대기, [STATUS.md](../STATUS.md)). 아래 문장은 그 뒤 실제 run 결과로 다시 고친다.

## 1. 신청서 초안

### Problem Definition

spec 13 §5 문안을 그대로 쓰되, 마지막 문장은 아직 검증 전이므로 바꿨다.

> 공장 IT 장애 대응에서 로그, GitHub 이슈, 수정 이력이 분리돼 있으면 같은 오류를 중복 조사하거나 이미 진행 중인 작업과 충돌할 수 있습니다. AI가 수정안을 만들어도 코드와 설비 중 어디를 확인해야 하는지, 실제 업무가 복구됐는지, 해결하지 못한 이유가 무엇인지 전달돼야 합니다. 과거의 실패를 성공 사례처럼 재사용하지 않는 대응 기록도 필요합니다. LineMedic은 이 문제를 합성 MES·설비 시뮬레이터 환경에서 다룹니다.

### Solution — 지금 남길 수 있는 문장

구현과 로컬 시험(fake·Docker)이 있는 문장만 남겼다. 괄호는 사람 확인용 표시이며 제출 전에 지우거나 live 결과로 바꾼다.

> LineMedic은 로그와 등록된 GitHub Issue를 단일 작업으로 연결합니다. 대응 이슈가 있으면 재사용하고, 없으면 생성한 뒤 작업 시작을 알립니다. *(W22~W26, fake 확인. 실제 GitHub는 G2·G10 뒤)*
> 수정안은 격리된 검사와 사람 리뷰·배포 승인을 거치며, 독립 업무 검증을 통과해야 복구로 기록합니다. *(W09~W12·W05, 로컬 Docker 확인. 실제 PR·배포는 G7·G8 뒤)*
> 결과는 검증된 성공·실패·미확인·권한 차단으로 구분해 다음 조사에 활용합니다. *(W27·W28, fake 확인)*

### 뺀 문장 (구현되지 않았거나 확인 전)

| spec 13 §5 문장 | 뺀 이유 | 다시 넣을 조건 |
|---|---|---|
| Nemotron 에이전트는 현재 로그·코드·설비 지표와 허용된 과거 사례를 조회해 코드 수정, 설비 점검 요청 초안, 진행 불가 보고를 구분합니다 | runtime adapter와 실제 모델 호출이 없다. 지금은 사람이 미리 쓴 제안만 돈다 | G3·G4 뒤 실제 모델 run(W14·W16) |
| 에이전트는 OpenShell 경계 안에서 동작하며 | OpenShell 정책·sandbox 구현이 없다 | G5 뒤 `sandbox_verified=true` run(W15) |
| 실제 PLC 제어와 자동 머지는 하지 않습니다 | 설계와 코드로는 맞지만(PLC 연결 없음, 머지는 사람) 실제 run으로 보인 적이 없다 | 실제 S1 run에서 사람 머지·승인 기록(W13) |

### Tech Stack — 실제로 쓴 것만

| 구분 | 내용 |
|---|---|
| 언어·서버 | Python 3.12, FastAPI(Starlette), pydantic, uvicorn, httpx |
| 저장 | SQLite(WAL). 사례 검색은 SQLite FTS5 lexical 검색이다 |
| 격리 | Docker 컨테이너(검사 runner·MES 배포 대상) |
| GitHub | REST API 연동 코드가 있으나 실제 호출은 아직 없다(fake·MockTransport 시험만) |
| NVIDIA | 없음. G3·G4·G5 뒤 실제로 쓴 모델 ID·endpoint·runtime·OpenShell 정책과 버전만 적는다. NAT·Guardrails는 실제 경로에서 쓴 경우만 적는다 |
| 알림 | GitHub Issue 댓글 경로만 있다(메일은 G12에서 정할 때만) |

## 2. 영상 구성표 (3분, spec 13 §4 기준)

각 장면은 **실제로 한 run의 화면·기록**만 쓴다. 지금은 모두 실제 run이 없다.

| 구간 | 장면 | 필요한 실제 run·기록 | 지금 상태 |
|---|---|---|---|
| 0:00~0:20 | 업무 장애와 이미 열린 Issue | S4-existing live run(W24) | NOT_RUN (G2·G10) |
| 0:20~0:40 | 기존 Issue 연결·시작 댓글 | 같은 run의 binding·시작 댓글 receipt | NOT_RUN |
| 0:40~1:25 | sandbox agent·과거 실패 근거·현재 코드·PR | S1 또는 S7-memory sandbox run, 봇 PR(W15·W28) | NOT_RUN (G3~G5·G10) |
| 1:25~1:50 | 사람 승인·exact image·업무 검사 | 같은 run의 사람 머지(G7)·배포 승인(G8)·verification | NOT_RUN |
| 1:50~2:10 | 설비 분기 또는 불가 보고 | S2-lite 또는 S6 run(W16·W29) | NOT_RUN |
| 2:10~2:30 | 잘못된 200·case 노트 | S1b run(W05)과 case note | 로컬 Docker 기록만(데모 호스트 아님) |
| 2:30~2:45 | S3 독립 보호 시험 | S3-B 표(있음), S3-C 판정 기록(G5 뒤) | S3-B만 있음 |
| 2:45~3:00 | 실제 평가 수·cold/memory 구분 | `make eval-summary` 결과 | 평가 run 없음(NOT_RUN) |

편집 규칙:

- 생략한 사람 승인·대기 시간은 자막으로 표시한다. 사람 없이 바로 끝난 것처럼 보이게 하지 않는다
- 여러 run의 장면을 한 번의 run처럼 이어 붙이지 않는다. 장면마다 run ID를 보인다
- 서비스 장애로 녹화본을 쓰면 녹화 시각을 표시하고 당시 실시간 실행이라고 하지 않는다
- 공식 영상 길이(R4)가 이 구성표보다 우선한다

## 3. 제출 전 점검 결과 (2026-09-28, 이 초안과 README 기준)

| 점검 | 결과 | 근거·남은 일 |
|---|---|---|
| 모델 산출물과 사람 산출물이 구분돼 있다 | 충족(초안) | 사람 제안은 `manual_integration`, 모델 산출물은 아직 없다고 README에 적었다 |
| 실제 runtime·sandbox 통합 수준이 그림·문구·영상과 일치한다 | 충족(초안) | runtime·sandbox 미통합이라고 적었다. 영상은 아직 없다 |
| Skill API·NeMo 조건(R1·R2)의 근거 또는 미확인 상태가 남아 있다 | 미확인 | [evidence/contest-conditions.md](../evidence/contest-conditions.md), G6 답변 대기 |
| cloud API 데모를 폐쇄망 실행이라고 쓰지 않았다 | 충족 | 추론 경로 자체가 아직 없다 |
| GPU·크레딧·상용 라이선스를 확인 없이 전제하지 않았다 | 충족 | 전제하는 문장이 없다 |
| 쓰지 않은 기술을 "사용"으로 적지 않았다 | 충족(초안) | NVIDIA 기술은 계획으로만 적었다 |
| S1/S2-lite/S1b/S3/S4~S7 중 실제 완료·미실행을 구분했다 | 충족 | 평가 run 없음(전부 NOT_RUN), [evidence/eval-summary.md](../evidence/eval-summary.md) |
| Issue 확인·시작 receipt·attempt 시각·PR·검사 identity가 연결된다 | 실제 run 없음 | 연결 기록 구조는 fake E2E로 확인(W13·W28). 실제 run 뒤 run-record로 다시 본다 |
| 댓글·메일 접수와 사람 수신·열람을 혼동하지 않았다 | 충족 | "댓글 등록"(접수)으로만 적는다 |
| case origin·snapshot·검색 엔진·cold/memory 분모를 명시했다 | 부분 | 검색 엔진(SQLite FTS5 lexical)은 적었다. snapshot·분모는 S7 run 뒤 |
| 사람 패치·승인·배포·S1b를 무인 agent 성과로 합산하지 않았다 | 충족 | 집계 규칙(W20)이 분모 밖으로 뺀다. 실제 집계는 run 뒤 |
| 회사·고객 데이터 없이 합성 환경임을 밝혔다 | 충족 | README 첫머리 |
| R1~R5 원문·답변 또는 미확인 상태를 보존했다 | 미확인 | G6 답변 대기 |
| 영상 길이·저장소 공개/심사자 접근·팀원 개별 제출을 확정 조건(R4·R5)에 맞췄다 | 미확인 | G6 답변과 사람 결정 대기 |
| docs/11 §4의 금지 표현이 없다 | 충족 | `make test`의 `unit/test_docs_consistency.py`가 README와 이 문서를 본다 |
