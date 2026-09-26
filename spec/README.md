# LineMedic Development Pack v4

> **설계·개발 명세 패키지. 제품 코드 구현·GitHub 연동·메일 전송·배포 완료본이 아니다.** 명령과 API는 구현 대상이며 실제 기능이 존재한다고 가정하지 않는다.

## 시작점

**[마스터 플랜](00-MASTER-PLAN.md)** → **[v3 검토·변경 보고서](V3-REVIEW-AND-V4-CHANGES.md)** → **[작업 계획](docs/10-delivery-plan.md)**.

v3의 MES·설비 판별, 실제 sandbox 에이전트, 봇 PR·사람 리뷰, exact SHA 배포, 독립 업무 검증을 유지했다. v4는 **로그와 GitHub Issue 두 입력을 연결하고, 작업 전 알림·차단 보고·성공/실패 사례 재사용을 추가**한다.

```text
로그 이상 → 기존 Issue 연결 / 없으면 생성 ┐
승인된 새 GitHub Issue → 주기적 접수      ├→ 단일 작업 선점 → 시작 알림 접수 확인
                                       ┘  → 사례 조회 → 조사·제안·검증
                                          → 결과·차단 사유 알림 → case note 저장
```

## 범위

한 repo, 등록 서비스 하나, 동시 agent 하나, 알림 채널 하나, SQLite exact/FTS 검색으로 시작한다. 시작 알림과 중복 작업 방지, 검증 결과의 신뢰 구분은 core다. webhook·SMTP와 메신저 동시 지원·임베딩/vector DB·자동 repair loop는 확장이다. 기본 GitHub 댓글 알림 대신 SMTP 하나를 선택할 수 있지만, 그때는 해당 어댑터의 실제 시험이 필요하다.

`[core]`는 사용자 요구를 충족하는 필수 구현, `[hardening]`은 추가 진단·자동 복구 편의, `[P1]`은 확장이다. **미완료 core는 hardening으로 재분류해 완료를 주장하지 않는다.** v4는 기능이 늘었으므로 기존 48시간 내 동일 완주 확률을 주장하지 않는다.

## 문서 지도

| 문서 | 기준 내용 |
|---|---|
| [00 마스터 플랜](00-MASTER-PLAN.md) | 제품·범위·핵심 workflow |
| [v3 검토 보고서](V3-REVIEW-AND-V4-CHANGES.md) | 실제 v3 포함 여부, 좋은 보강, 추가 수정 근거 |
| [01 요구사항](docs/01-requirements.md) | FR·INV·AC, 각 요구의 완료 조건 |
| [02 아키텍처](docs/02-architecture.md) | 같은 Control Plane 안의 모듈·신뢰 경계 |
| [03 API](docs/03-api-contracts.md) | `linemedic.v4`, API·입출력·오류·멱등성 |
| [04 데이터·상태](docs/04-data-state.md) | 전체 fresh DDL, incident와 work 상태·전이 |
| [05 에이전트](docs/05-agent-spec.md) | 도구·sandbox·역사 자료·프로포절 경계 |
| [06 브로커·러너](docs/06-broker-runner.md) | 보호된 패치 검사·Issue에 연결된 PR |
| [07 보안](docs/07-security.md) | 인증·생성 코드 격리·Issue/알림/기억 위협 |
| [08 배포·검증](docs/08-release-verification.md) | 승인한 SHA·image·업무 계약 |
| [09 평가](docs/09-scenarios-evaluation.md) | S1~S7, 중복·알림·기억 시험과 평가 분리 |
| [10 개발 계획](docs/10-delivery-plan.md) | 기존 W 작업 보존, W22~W29, 선행 관계·남은 시간 |
| [11 Runbook](docs/11-runbook.md) | config·목표 CLI·운영자 확인·reset |
| [12 NVIDIA 조건](docs/12-nvidia-requirements.md) | 인정 여부·런타임·외부 서비스 실제 스파이크 |
| [13 데모·제출](docs/13-demo-submission.md) | 화면·발표·상태·주장 경계 |
| [14 결정·출처](docs/14-decisions-sources.md) | v2/v3 계승과 v4 결정, 공식 문서 |
| [15 Issue intake](docs/15-issue-intake-workflow.md) | 검색·생성·재사용·감시·허가·중복·PR 충돌 |
| [16 알림](docs/16-notifications.md) | 시작 게이트·outbox·실패 사유·receipt |
| [17 사례 기억](docs/17-case-memory.md) | 정답/오답/차단 구분, lexical RAG·재검증·평가 누수 |
| [18 전환·검증](docs/18-migration-validation.md) | v3→v4 호환성·데이터 전환·문서 검증 범위 |
| [작업 인계](templates/implementation-handoff.md) | 개발자·코딩 에이전트에 한 작업 맡기기 |
| [실행 기록](templates/run-record.md) | 실제 run·Issue·알림·기억·검사 원본 |
| [차단 보고](templates/blocker-report.md) | 못 하는 이유와 다음에 필요한 사람의 조치 |
| [사례 노트](templates/case-note.md) | 오류·가설·변경·검사·결과를 구조화 |
| [패키지 검증 결과](PACKAGE-VALIDATION.md) | 이 패키지 생성 시 수행한 문서 검사와 미검증 범위 |

## 읽는 순서

전원 00 → 검토 보고서 → 10 → 01. 백엔드는 15·16·17 → 03·04 → 06·08. 에이전트는 03·05·17. 인프라는 02·07·11·12. QA는 08·09. 발표는 13을 읽는다. 필요한 계약만 나눠 읽고 전체 문서가 구현됐다고 가정하지 않는다.

## 변경 규칙

API 계약은 의도적으로 `linemedic.v4`로 올렸다. 기존 `linemedic.v2` client와 혼용하지 않는다. 03·04·관련 테스트·runtime manifest를 같은 변경에서 갱신한다. v3 원본 ZIP은 변경하지 않았으며 검토 보고서에 파일별 출처를 남겼다.

제품 사실은 코드와 실제 실행으로 확인한다. 문서 내 DDL·JSON·YAML의 구문 검사는 제품 E2E, 공식 SDK 호환성, 메일 배달, GitHub 권한, 대회 참가 조건 확인을 대신하지 않는다.
