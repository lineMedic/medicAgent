# W00 — 데모 호스트 확정과 host manifest

| 항목 | 값 |
|---|---|
| 등급 | core |
| 자율성 | H(G1) — 에이전트는 manifest 생성과 점검만 |
| 선행 | B00 |
| 목표 상태 | LIVE_VERIFIED (확정 호스트에서 만든 manifest가 있음) |
| 원본 근거 | [spec 02 §7](../spec/docs/02-architecture.md), [spec 11 §1](../spec/docs/11-runbook.md), [spec 12 §5 N08·N09](../spec/docs/12-nvidia-requirements.md), [spec 14 W10](../spec/docs/14-decisions-sources.md#w10) |
| 요구 | FR-16 |

## 목표

모든 평가를 실행할 호스트 1대가 정해지고, 그 호스트에서 만든 `evidence/host-manifest.json`이 있다.

## 사람이 할 일 (G1)

- 호스트 선택: OS·kernel·CPU arch·메모리·디스크, 다른 운영 workload가 없는지, OpenShell Support Matrix와 맞는지.
- `DEMO_HOST_ID`를 정하고 `.env`에 넣는다.
- 에이전트가 그 호스트에서 명령을 실행할 수 있게 하거나, 사람이 `make host-manifest > evidence/host-manifest.json`을 실행한다.

## 에이전트가 할 일

1. 게이트 요청을 [docs/10](../docs/10-human-gates.md) 형식으로 전달하고 STATUS.md의 G1을 `REQUESTED`로 기록한다.
2. 호스트에서 `make setup`, `make doctor`, `make host-manifest`를 실행해 결과를 `evidence/host-manifest.json`에 저장한다(비밀·환경변수 값 없음).
3. Docker Engine/Compose 버전, Python 버전을 DECISIONS.md D43 행 아래에 "실제 버전" 메모로 남긴다.
4. 호스트가 개발 머신과 다르면, 이후 docker·live 테스트는 이 호스트에서만 실행한다고 STATUS.md W00의 메모에 적는다.

## 수용 기준

- manifest에 OS, kernel, arch, Python, SQLite(FTS5 여부), Docker, git 버전이 있고 `DEMO_HOST_ID`와 생성 시각(UTC)이 있다.
- OpenShell·runtime 버전은 설치 전이면 `null`이고, G5 이후 다시 생성한다.

## 완료 증거

`evidence/host-manifest.json` 경로와 sha256, 사람이 호스트를 확정한 시각을 STATUS.md G1의 확인 근거·완료 시각에 기록.

## 금지·함정

- 호스트 후보 목록이나 README 요구사항만 보고 "지원됨"으로 적지 않는다. N09에서 실제 sandbox 기동으로 확인한다(W02).
- 평가 시작 후에는 호스트를 바꾸지 않는다. 바꾸면 이전 결과와 합산하지 않는다.
