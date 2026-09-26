# W11 — 봇 PR 생성·결과 불명 기록·reconcile CLI

| 항목 | 값 |
|---|---|
| 등급 | core (자동 bounded reconcile은 H04) |
| 자율성 | C(G2·G10) — FakeGitHub로 UNIT_TESTED, live PR은 게이트 후 |
| 선행 | W10, W24~W26 (live 실행 조건) |
| 목표 상태 | LIVE_VERIFIED |
| 원본 근거 | [spec 06 §6·§8·§10](../spec/docs/06-broker-runner.md), [spec 15 §7](../spec/docs/15-issue-intake-workflow.md), [spec 04 §4·§7·§8](../spec/docs/04-data-state.md), [spec 14 W18](../spec/docs/14-decisions-sources.md#w18) |
| 참조 | [docs/05 ⑤·⑦](../docs/05-workflows.md), DECISIONS D46 |
| 요구·테스트 | FR-06, FR-11, INV-06 / T-EXEC-01, T-IDEM-01(PR 경로) |

## 목표

R2까지 통과한 candidate를 봇 credential로 `autofix/<run>/<incident>/<proposal>`에 push하고 `baseline/<run>`을 대상으로 PR을 연다. PR head가 candidate SHA와 같은지 읽어서 확인한다. 응답이 불명이면 UNKNOWN으로 두고 운영자가 `make reconcile`로 조정한다.

## 만들 파일

- `linemedic/control_plane/broker/github_pr.py`
  - 사전 조건: work가 Issue에 bound, 시작 알림 ACCEPTED, 현재 Issue 승인 snapshot 유효. **외부 생성 직전** Issue state·assignee·기존 PR 충돌 재조회
  - 같은 work·candidate의 PR이 있으면 재사용. 타인의 PR·브랜치는 덮어쓰지 않는다
  - `TX{ executions CREATE_PR INTENDED (logical_key pr:<work>:<proposal>:<candidate_sha>) }` → push(고정 argv `git push`, credential은 `GIT_ASKPASS` 스크립트가 env에서 읽음, 명령줄·로그에 비노출) → `create_pull` → `get_pull`로 head SHA 확인 → `TX{ SUCCEEDED, incident PR_OPENED, work WAITING_REVIEW, PR_READY intent, case event(UNVERIFIED) }`
  - PR 본문: [spec 06 §8](../spec/docs/06-broker-runner.md) 템플릿 + `Related to #<n>`, work/generation/run/incident, base·candidate, 검사 ID, "보장하지 않는 것" 절. `Fixes`·`Closes`·`Resolves`를 넣지 않는다(검사로 확인). 비밀·내부 경로 정제
  - timeout·불명 → `TX{ execution UNKNOWN, incident·work EXECUTION_UNKNOWN }`
- `linemedic/control_plane/broker/reconcile.py` — `reconcile_execution(execution_id)`: CREATE_PR은 head 브랜치·base·marker·candidate SHA가 모두 일치하고 작성자가 봇인 PR만 `FOUND` → PR_OPENED/WAITING_REVIEW. `CONFIRMED_ABSENT`·`CONFLICT`·`INCONCLUSIVE`는 기록만 하고 새 PR을 만들지 않는다(무변경이 확인되면 ESCALATED). CREATE_ISSUE·DEPLOY 분기는 각각 W24·W12가 채운다
- `ops_api.py` 추가 — `POST /ops/executions/{id}/reconcile`
- CLI·Makefile — `make reconcile RUN_ID= EXECUTION_ID=`
- 테스트: `integration/test_github_pr.py`, `integration/test_execution_unknown.py`

## 수용 기준

- T-EXEC-01: PR 생성 직후 timeout(부작용 있음) → UNKNOWN, 두 번째 생성 호출 0회. reconcile → FOUND → PR_OPENED.
- T-IDEM-01(PR 경로): 같은 제안을 두 번 처리해도 PR 1개.
- 사전 조건 하나라도 불충족(receipt 없음, Issue closed, 사람 assignee) → PR 생성 0, 사유 기록.
- PR 본문에 closing keyword가 없고 `Related to #`가 있다.
- 다른 작성자가 같은 브랜치명·marker로 만든 PR → 채택하지 않음.
- live(G2·G10): 전용 repo에서 봇 PR 1개, head SHA = candidate SHA, 리뷰어가 봇이 아님을 기록.

## 금지·함정

- 봇 credential로 머지·승인·브랜치 보호 변경을 하지 않는다.
- "한 번 조회해서 없었으니 다시 만든다"를 하지 않는다.
- PR 제목·브랜치명·파일명을 shell 문자열로 조합하지 않는다.
