# S5-new: 새 Issue polling 감지

- repo: lineMedic/l3-mes-api (ID 1392050186)
- Issue: #8, 생성 시각(GitHub): 2026-09-28T10:10:51Z
- 감지 시각(UTC): 2026-09-28T10:11:37.871703Z
- work: WORK-718DDB01769D (WAITING_APPROVAL, 자동 승인은 W25)
- poll 간격: 60초 (탐지 SLA 아님)
- 작성자: `daejung-kim96` (ID 132763253, `ISSUE_TRUSTED_AUTHOR_IDS`에 있음, 봇 `jgoneit`과 다름), open, 라벨 없음 — 읽기 조회로 확인
- 실행: `LINEMEDIC_LIVE_S5=1 LINEMEDIC_LIVE_S5_TIMEOUT=900 pytest ...::test_new_issue_from_trusted_author_is_detected_by_polling` → 1 passed (364.85s). 관찰(initial import) 뒤 팀원이 Issue를 만들었다. 개발 Mac, D94 구성(봇 = `jgoneit`)
