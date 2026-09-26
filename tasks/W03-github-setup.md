# W03 — GitHub 조직·데모 repo·봇·보호 규칙 (N07)

| 항목 | 값 |
|---|---|
| 등급 | core |
| 자율성 | C(G2) — 점검·시드 스크립트는 A, 생성·설정은 사람 |
| 선행 | B00 (시드 push는 W04 이후) |
| 목표 상태 | LIVE_VERIFIED |
| 원본 근거 | [spec 02 §4·§5](../spec/docs/02-architecture.md), [spec 06 §6](../spec/docs/06-broker-runner.md), [spec 11 §6](../spec/docs/11-runbook.md), [spec 12 §5 N07](../spec/docs/12-nvidia-requirements.md), [spec 14 W04·W11·W12](../spec/docs/14-decisions-sources.md#w11) |
| 요구 | FR-06 (준비), INV-02 |

## 목표

봇 credential이 만든 PR을 다른 사람이 리뷰해야만 머지할 수 있고, `baseline/*` 브랜치에 봇이 직접 push할 수 없으며, squash 머지만 허용된다는 것을 **실제로 시험**한 기록이 있다. 데모 repo에 버그 base 커밋이 있다.

## 사람이 할 일 (G2)

1. 팀 조직에 `l3-mes-api` repo를 만든다.
2. 봇 계정 또는 GitHub App을 만들고 이 repo에만 metadata·Issues·Pull requests·필요한 contents 권한을 준다. 팀원 개인 PAT를 쓰지 않는다(PR 작성자는 자기 PR을 승인할 수 없다).
3. 봇이 아닌 리뷰어 계정을 정한다.
4. `baseline/*` 패턴 보호: 필수 리뷰 1, 최신 변경 승인(stale approval 폐기), 봇 직접 push 금지, bypass 없음. `*`는 `/`와 일치하지 않으므로 run_id에 `/`를 쓰지 않는다.
5. repo 머지 설정: squash만 허용.
6. `.env`에 `GITHUB_BROKER_CREDENTIAL`(봇), `GITHUB_SETUP_CREDENTIAL`(baseline 브랜치 생성용)을 넣고, repo 이름·숫자 ID·리뷰어 계정명을 에이전트에게 알린다.

## 만들 파일

- `linemedic/scripts/github_setup_check.py` — 읽기 전용 점검: repo 숫자 ID와 이름 일치, 봇 identity, 봇 권한 범위, 머지 설정(squash only), `baseline/*` 보호 규칙 존재와 내용. 결과를 `evidence/github-setup-check.json`에
- `linemedic/scripts/github_protection_probe.py` — 쓰기 시험(G10 + 사용자 명시 허락 후만): ① setup credential로 `baseline/r-probe-<ts>` 생성 ② 봇으로 그 브랜치에 직접 push → 거절 기대 ③ 봇 PR 생성 → 리뷰 없이 머지 시도 → 거절 기대 ④ 결과 기록. 만든 probe 브랜치·PR은 삭제하지 않고 `probe` 라벨을 붙여 둔다
- `linemedic/scripts/seed_demo_repo.py` 의 push 부분 (시드 생성은 W04) — W04의 시드로 만든 버그 base 커밋을 원격 `main`에 push(최초 1회, 사람 허락 후). 이후 `BASELINE_COMMIT`을 `.env`에 적도록 안내
- doctor 항목 `github`(credential 존재, repo ID 일치)

## 구현 단계

1. 점검 스크립트를 FakeGitHub 없이도 테스트할 수 있게 응답 파싱 함수를 분리하고 단위 테스트를 쓴다.
2. G2 요청을 보낸다.
3. G2가 열리면 `github_setup_check.py`를 실행한다.
4. 사용자에게 쓰기 시험 허락을 받은 뒤 `github_protection_probe.py`를 실행한다.
5. W04가 끝났으면 시드 push를 수행하고 원격 main의 커밋 SHA(40자)를 `BASELINE_COMMIT`으로 기록한다.

## 수용 기준 (N07)

- 봇 PR을 다른 팀원이 승인할 수 있다.
- 리뷰 없는 머지가 거절된다.
- 봇의 `baseline/*` 직접 push가 거절된다.
- squash 외 머지 방식이 꺼져 있다.
- 실제 적용된 설정·bypass 여부가 run manifest에 들어갈 수 있게 evidence에 있다.

## 금지·함정

- 에이전트가 보호 규칙을 만들거나 바꾸지 않는다. 설정은 사람이 한다.
- 전용 데모 repo 밖에 쓰지 않는다. 원격 main을 force push하거나 되돌리지 않는다.
- 보호가 확인되기 전에는 "승인 배포 완료"를 주장하지 않는다.
