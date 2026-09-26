# 00. 미션과 범위

> 원본: [spec 00 마스터 플랜](../spec/00-MASTER-PLAN.md), [spec 01 요구사항](../spec/docs/01-requirements.md), [검토 보고서](../spec/V3-REVIEW-AND-V4-CHANGES.md)

## 1. 한 문장 정의

LineMedic은 **서버 로그 또는 등록된 GitHub Issue를 받아, 같은 작업을 중복으로 만들지 않고 담당 Issue에 연결하고, 작업 시작을 먼저 알린 뒤 코드 수정 제안 또는 설비 점검 요청 초안을 만들며, 결과와 실패 원인을 검증 수준과 함께 저장해 다음 조사에 재사용하는** 공장 IT 에이전트다.

- 대회: Korea Agentic AI Hackathon (패스트캠퍼스 × NVIDIA) 온라인 예선.
- 환경: 가상 3라인(L3), 합성 MES 로그, 가짜 카메라 지표, 팀 소유 데모 저장소 `l3-mes-api` 하나.
- 범위 밖: 실제 공장·고객 데이터, 실제 정비 지시 발송, PLC 접속·제어.
- "대응 이력 재사용"은 모델 재학습이 아니다. 검색한 사례를 근거로 읽고 **현재 상황에서 다시 검증**하는 것이다.

## 2. 전체 흐름

```text
로그 이상 → 기존 Issue 연결 / 없으면 생성 ┐
승인된 새 GitHub Issue → 주기적 접수      ├→ 단일 work 선점 → 시작 알림(접수 확인)
                                       ┘  → 사례 조회 → 에이전트 조사(sandbox)
                                          ├ 코드: 보호된 검사 → 봇 PR → 사람 리뷰·머지
                                          │        → exact SHA 배포 승인 → 독립 업무 검증
                                          ├ 설비: 정비 요청 초안(서버 변경 없음)
                                          └ 불가: 차단 보고
                                          → 결과·차단 알림 → case note 저장
```

단계별 상세는 [05-workflows.md](05-workflows.md)에 있다.

## 3. 시나리오

| ID | 장면 | 도착점 | 성공으로 오해하면 안 되는 것 |
|---|---|---|---|
| S1 | 코드 오류 | Issue → 시작 알림 → 실제 agent 패치 → PR → 사람 승인 → 업무 검증 PASS | PR 생성·머지·test PASS만으로는 복구가 아님 |
| S2-lite | 설비 점검 | 같은 Issue에 정비 요청 초안·확인 사항 | 초안 작성 ≠ 설비 복구 |
| S1b | 거짓 정상 | HTTP 200이지만 틀린 집계를 verifier가 거절 | 사람이 주입한 결함. agent 성능에 포함하지 않음 |
| S3 | 안전성 | agent 반응(A)·broker 거절(B)·sandbox 프로브(C)를 분리 기록 | 연결 실패 전체가 정책 차단은 아님 |
| S4 | Issue 연결 | 신규 생성, 기존 재사용, 모호함·조회 불완전 시 안전 정지 | 제목이 비슷하다고 같은 원인으로 확정하지 않음 |
| S5 | Issue 입력 | 승인된 새 Issue를 작업으로 접수, 시작 알림보다 빠른 수정 0건 | 공개 Issue를 무조건 실행하지 않음 |
| S6 | 불가 보고 | 이유·근거·해본 일·필요 조치가 있는 외부 알림 | 로컬 이벤트 저장만으로는 발송이 아님 |
| S7 | 사례 재사용 | 검증된 성공·실패 사례를 찾아 현재 판단에 인용 | 과거 정답이 현재도 맞는다는 보장 없음 |

S4~S7은 새 서비스가 아니라 S1·S2에 붙는 입구·출구다.

## 4. 범위 등급

| 등급 | 뜻 | 미완료 시 |
|---|---|---|
| `[core]` | 사용자 요구를 충족하는 필수 구현 | 완료를 주장하지 않는다. hardening으로 재분류해 숨기지 않는다 |
| `[hardening]` | core 이후의 진단·자동 복구 편의(H03~H07) | "설계됨/미구현"으로 공개 |
| `[P1]` | 확장 | 구현하지 않는다 |

### core-v4 범위

- 한 repo, 서비스 매핑 하나, 동시 에이전트 1개, Issue polling 60초, 알림 채널은 GitHub Issue 댓글 1개.
- FastAPI + SQLite. 사례 검색은 exact fingerprint + SQLite FTS5(lexical RAG). 임베딩 검색이라고 부르지 않는다.
- H01(claim 경합)과 H02(같은 멱등키·다른 본문 409)는 **core로 승격**(W25).
- 시작 알림 확인 전 수정 금지, 결과 불명 시 재조회, 사례 신뢰 수준 구분은 core.
- 로그 관찰 가능성을 확인하지 못하면 검증을 PASS로 만들지 않는 것도 core.

### hardening (H03~H07)

H03 로그 heartbeat·cursor, H04 자동 bounded reconcile, H05 빌드 레시피 hash, H06 추가 테스트 조합, H07 상세 화면. [tasks/H03-H07-hardening.md](../tasks/H03-H07-hardening.md) 참조.

### P1 (만들지 않는다)

공개 HTTPS webhook, 다중 저장소, 알림 채널 동시 발송, 임베딩·vector DB·reranker, 자동 정비 완료 수집, CMMS, 자동 재조사·머지 감시, 온프레미스 NIM, 비평가(critic) 에이전트, 두 번째 버그 유형.

### 계속 금지

자동 머지, 사람 승인 없는 배포, 모델이 지정한 수신자·웹훅 URL, 타인 작업 강탈, 실제 PLC 접속·제어, 회사·고객 데이터, 보호 테스트 수정, 무한 repair loop, 기존 Harness Toolkit 재설계.

## 5. 네 가지 분리

| 대상 | 뜻 | 서로 섞으면 안 되는 이유 |
|---|---|---|
| GitHub Issue | 협업 티켓 | Issue close ≠ incident RESOLVED |
| incident | 관찰된 장애 | 상태 enum에 GitHub 상태를 넣지 않음 |
| work item / attempt | 수행 단위 / 한 번의 모델 세션 | 재시도는 새 generation |
| case note | 과거 결과 기록 | test만 통과한 PR은 정답노트가 아님 |

알림 발송 실패가 이미 확인된 업무 복구를 취소하지 않고, 알림 성공이 복구를 만들지도 않는다.

## 6. 검증 주장의 한계

| 관찰 | 보장 범위 | 보장하지 않는 것 |
|---|---|---|
| Issue binding·완료된 조회 | 등록 scope에서 해당 티켓에 연결한 근거 | 모든 자연어 Issue의 완벽한 중복 탐지 |
| 시작 알림 receipt | 수정 전에 공급자가 알림 요청을 접수 | 사람이 읽음, 메일 inbox 도착 |
| 재현 실패 → 패치 통과 | 코드 변경에 반응하는 테스트 | 코드가 유일한 원인임 |
| 업무 verifier PASS | 특정 image·contract·관찰 구간 통과 | 전체 운영 안정성, 안전 인증 |
| 사례 검색 | 과거에 기록·검증된 조건과 결과 | 자동 학습, 새 장애의 자동 정답 |

## 7. "완료가 아닌 것"

[spec 01 §6](../spec/docs/01-requirements.md)을 그대로 따른다.

- 문서만 있음, mock API만 성공, 사람이 쓴 패치만 성공, local 모드만 성공, PR만 생성, 컨테이너만 healthy, sandbox 프로브만 성공, 기술 로고만 표시, 대회 문의만 전송.
- Issue 링크 없이 패치부터 생성, 로컬 알림 이벤트만 저장, 과거 로그만 쌓고 검색하지 않음, 정답/오답을 LLM 자기보고로 분류.

## 8. 요구사항 ID 색인

기능 요구 FR-01~FR-26, hardening HR-01~HR-07, 불변식 INV-01~INV-16, 수용 시나리오 AC-S1·AC-S2·AC-S1B·AC-SBX·AC-S3~AC-S7은 [spec 01](../spec/docs/01-requirements.md)에 있다. 각 ID를 어느 카드가 구현하는지는 [08-task-plan.md §4](08-task-plan.md)에 있다.
