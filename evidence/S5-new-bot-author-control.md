# S5-new live 대조 — 봇(=개인 계정) 작성 Issue는 work를 만들지 않음 (D94)

| 항목 | 값 |
|---|---|
| 날짜 (UTC) | 2026-09-28T09:59Z ~ 10:00Z |
| 실행자 | 에이전트(Claude Code 세션), 사용자 지시("상황을 가정해 테스트용 Issue를 만들고 진행") |
| 환경 | 개발 Mac, repo `lineMedic/l3-mes-api` (ID 1392050186), 봇 credential = `jgoneit`(ID 87176677, D94) |
| 조립 | `test_issue_live.py::test_new_issue_from_trusted_author_is_detected_by_polling`과 같은 조립(임시 DB, scope `eval:r-20260928-000000-5b07`, `ISSUE_INTAKE_ENABLED=true`)을 세션 임시 스크립트로 실행: 관찰(initial import) → 봇 credential로 Issue 생성 → `poll_once` 반복 |
| 판정 | **S5-new 통과 조건 NOT_MET(구성상 불가)**. polling 감지와 봇 작성 제외는 설계대로 동작 |

## 결과

| 단계 | 값 |
|---|---|
| 관찰 | `initial_import` 2026-09-28T09:59:32.105Z |
| Issue 생성 | #7 "L3 불량 요약 화면 응답 지연 문의", GitHub `created_at` 2026-09-28T09:59:35Z, 작성자 87176677 |
| mirror 반영 | 2026-09-28T10:00:37.989Z (poll 간격 기준, 탐지 SLA 아님) |
| work | 0건 |
| audit | `ISSUE_BOT_WITHOUT_WORK` (`linemedic/control_plane/issue_sync.py`: 작성자 = 봇이면 승인 작성자 확인 전에 work 없이 넘김) |

## 해석

- `ISSUE_TRUSTED_AUTHOR_IDS`에는 3개 ID(87176677 포함)가 있지만, 봇도 87176677이라 이 계정이 만든 Issue는 봇 작성으로 판정된다. 봇이 자기 Issue로 작업을 다시 시작하는 고리를 막는 규칙이라 코드를 바꾸지 않는다.
- S5-new 통과(승인 작성자 Issue → work WAITING_APPROVAL)를 확인하려면 봇이 아닌 승인 작성자(117441212 또는 132763253)가 Issue를 만들고 `LINEMEDIC_LIVE_S5=1 make test-live`를 실행해야 한다. 봇을 분리하면(D94 대체) `jgoneit`도 승인 작성자로 쓸 수 있다.
