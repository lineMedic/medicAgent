# W22 — GitHub 연동 포트·등록 범위·route catalog (N11)

| 항목 | 값 |
|---|---|
| 등급 | core |
| 자율성 | C(G2·G10) — 포트·fake·설정은 A, live smoke는 게이트 후 |
| 선행 | W06 (live는 W03) |
| 목표 상태 | LIVE_VERIFIED (fake 부분은 UNIT_TESTED로 먼저 닫고 다음 카드 진행 가능) |
| 원본 근거 | [spec 15 §2](../spec/docs/15-issue-intake-workflow.md), [spec 16 §1](../spec/docs/16-notifications.md), [spec 02 §4](../spec/docs/02-architecture.md), [spec 12 §8 N11](../spec/docs/12-nvidia-requirements.md), [spec 14 W13·W14·W15](../spec/docs/14-decisions-sources.md#w13) |
| 참조 | DECISIONS D46, [docs/10 G2·G10](../docs/10-human-gates.md) |
| 요구 | FR-17·FR-18·FR-20 준비, INV-13, INV-15 |

## 목표

모든 GitHub 호출이 `GitHubPort` 하나를 거치고, 테스트는 `FakeGitHub`로 실패 상황(timeout·403·429·잔여 페이지·PR 섞인 목록)까지 재현한다. 등록 repo·자동 처리 작성자·알림 route는 host 설정에서만 온다. live에서는 전용 repo의 조회·Issue 생성·댓글이 각 1회 성공한다.

## 만들 파일

- `linemedic/integrations/github.py`
  - `GitHubPort` 메서드: `get_repo()`, `get_identity()`, `list_issues(state, since, sort, direction, per_page, page, etag)`, `get_issue(number)`, `create_issue(title, body, labels)`, `list_issue_comments(number, since, page)`, `create_issue_comment(number, body)`, `list_pulls(head=None, state)`, `get_pull(number)`, `create_pull(head, base, title, body)`
  - 응답에 rate limit 정보(`remaining`, `reset`, `Retry-After`)와 ETag를 담는다
  - 오류 타입: `RateLimited(retry_after)`, `Forbidden`, `NotFound`, `Conflict`, `Unknown(observation)`(timeout·연결 끊김 — 부작용 여부 불명)
  - `HttpGitHub`: httpx, base URL·API 버전 헤더는 config, credential은 env. 요청 대상 repo는 config의 등록 repo로 고정(인자로 받지 않음)
  - `FakeGitHub`: 메모리 상태, 페이지네이션, PR 항목 포함, 장애 주입(`fail_next(kind, after_side_effect=True/False)`)
- `config/linemedic.toml` — `repository.{id, full_name, service_id}`, `issue_intake`(spec 15 §2 YAML 예시와 같은 구조의 TOML), `notifications.routes`(spec 16 §1 YAML 예시와 같은 구조의 TOML), `github.api_version`(N11에서 확정할 값. 확정 전에는 **키를 생략**한다 — TOML에는 null이 없음, D60)
- `linemedic/control_plane/catalog.py` — repo·service·route·equipment catalog 로더. 알 수 없는 repo·route ID 거부
- `linemedic/scripts/doctor.py` 추가 — `github` 항목(credential 존재, repo ID 일치, bot identity)
- `linemedic/tests/live/test_github_smoke.py`(`live_github`)
- 테스트: `unit/test_github_port.py`, `unit/test_catalog.py`

## 구현 단계

1. `FakeGitHub`와 포트 계약 테스트를 먼저 쓴다. 같은 계약 테스트를 `HttpGitHub`에는 live 마커로 적용한다.
2. `HttpGitHub`를 구현한다. 비신뢰 문자열을 URL path에 이어 붙이지 않는다(번호는 int로 검증).
3. catalog 로더를 구현한다. `ISSUE_TRUSTED_AUTHOR_IDS`는 **숫자 ID 목록**으로만 파싱한다(login 문자열 불가).
4. shadow 모드 플래그 `github.write_enabled`(기본 false, G10에서 사람이 켬)를 둔다. false면 쓰기 메서드는 "계획"만 반환하고 실제 호출하지 않는다.
5. G2·G10 이후 live smoke: repo ID 일치, 목록 조회, 테스트 Issue 1개 생성(`linemedic-smoke` 라벨), 댓글 1개. receipt(Issue number·node ID·comment ID)를 `evidence/N11-github-smoke.md`에 기록. `X-GitHub-Api-Version` 값을 확정해 config에 적는다.

## 수용 기준

- fake: 3페이지 목록, PR 항목 포함, 두 번째 페이지 403, 생성 후 timeout(부작용 있음) 각각 정확한 오류 타입.
- 등록 repo 밖을 가리키는 요청을 만들 수 없다(API에 repo 인자 없음).
- `write_enabled=false`에서 쓰기 호출 0회.
- live: 전용 repo에서 조회·생성·댓글 각 1회 receipt.

## 금지·함정

- 전용 데모 repo 밖에 어떤 요청도 보내지 않는다.
- credential·Authorization 헤더를 로그·오류 메시지에 남기지 않는다.
- Search API 결과 0개를 "없음"으로 쓰지 않는다(W24 규칙).
