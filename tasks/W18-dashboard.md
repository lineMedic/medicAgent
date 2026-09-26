# W18 — 최소 화면 1개 (읽기 전용)

| 항목 | 값 |
|---|---|
| 등급 | core 최소판 (상세 화면은 H07) |
| 자율성 | A |
| 선행 | W06 (표시할 데이터는 이후 카드에서 늘어남 — W26 이후 한 번 더 점검) |
| 목표 상태 | UNIT_TESTED |
| 원본 근거 | [spec 13 §1·§2](../spec/docs/13-demo-submission.md), [spec 07 §3](../spec/docs/07-security.md), [spec 01 FR-13](../spec/docs/01-requirements.md), [spec 12 §3.1](../spec/docs/12-nvidia-requirements.md) |
| 참조 | [docs/11 §5](../docs/11-definition-of-done.md), DECISIONS D49 |
| 요구·테스트 | FR-13 / T-UI-01 |

## 목표

DB를 읽기 전용으로 여는 localhost 화면 하나가 run의 실제 상태를 보여 준다. 모르는 값은 "미확인"으로 표시하고, 상태 문구는 과장 없이 정해진 표현을 쓴다.

## 만들 파일

- `linemedic/dashboard/__main__.py` — `python -m linemedic.dashboard` → `127.0.0.1`에만 bind, SQLite `file:...?mode=ro` URI로 연결, GET만 제공, 쓰기 route 없음
- `linemedic/dashboard/readmodel.py` — 화면 데이터 조립(`/ops/dashboard` JSON에도 같은 함수 사용)
- `linemedic/dashboard/templates/index.html` — Jinja2 autoescape. JS에서 token 저장·API 쓰기 없음
- `ops_api.py` 추가 — `GET /ops/dashboard`(operator read)
- 테스트: `unit/test_dashboard_escape.py`, `unit/test_dashboard_readmodel.py`

## 화면 구성 ([spec 13 §1](../spec/docs/13-demo-submission.md))

- **상단**: run ID, 실제 호스트 ID, runtime·version, `agent_mode`·sandbox 상태(`sandbox_verified`), 모델 ID, memory mode·snapshot, repo, polling 마지막 성공 시각(KST 표시).
- **본문**: Issue/work 카드(`Issue #n · WORK-…/g1 · INC-… · service`, 연결 basis, work 상태, 업무 검증 상태, 시작 알림 receipt, 메일 전달 여부) + 타임라인(Issue 연결 → 시작 알림 접수 → agent 시작 → 사례/근거 조회 → 테스트·패치 검사 → PR → 사람 승인 대기 → 배포 → 검증).
- **오른쪽**: 근거(evidence), case note(outcome과 "현재도 틀린 패치라는 뜻 아님" 같은 해석 경계), 알림 상태(PENDING·UNKNOWN·FAILED 포함), 도구 trace 요약(모델이 고른 도구 순서 — 심사 1번 항목 증거).
- 상태 문구는 [docs/11 §5](../docs/11-definition-of-done.md) 표를 그대로 쓰는 매핑 함수로.

## 수용 기준

- T-UI-01: 로그·Issue 제목·case 본문에 `<script>`·`<img onerror>`·`javascript:` 링크가 있어도 문자열로 표시되고 실행되지 않는다.
- 값이 없으면 "미확인", 해당 없음은 "N/A"로 구분된다.
- `RESOLVED`와 `notification FAILED`가 함께 있을 때 둘 다 그대로 표시된다.
- `WAITING_REVIEW`를 "자동 복구 완료"로, notification ACCEPTED를 "읽음"으로 표시하지 않는다(매핑 테스트).
- 서버가 `0.0.0.0`에 bind하지 않고, DB 쓰기를 시도하면 실패한다(읽기 전용 연결).

## 금지·함정

- 장식 기능·차트 확장은 하지 않는다(시간 부족 시 먼저 줄이는 항목).
- native thought 전체를 보여 주지 않는다. 도구·근거·짧은 판단 요약만.
- 임의 링크를 자동 호출(미리보기 fetch)하지 않는다.
