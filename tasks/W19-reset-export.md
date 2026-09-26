# W19 — run-new·reset·export·archive

| 항목 | 값 |
|---|---|
| 등급 | core |
| 자율성 | C(G2) — 로컬 부분은 A, baseline 브랜치 생성은 G2 후 |
| 선행 | W06, W11 |
| 목표 상태 | LIVE_VERIFIED |
| 원본 근거 | [spec 11 §4·§7·§9](../spec/docs/11-runbook.md), [spec 04 §9](../spec/docs/04-data-state.md), [spec 03 §5 runs](../spec/docs/03-api-contracts.md), [spec 09 §9](../spec/docs/09-scenarios-evaluation.md), [spec 17 §7](../spec/docs/17-case-memory.md) |
| 참조 | [docs/05 ⑨](../docs/05-workflows.md), DECISIONS D50·D52 |
| 요구·테스트 | FR-12 / T-RESET-01 |

## 목표

매 평가 run이 새 run ID·새 routing scope·새 baseline 브랜치·새 workspace로 시작하고, 이전 run의 원본 증거·원격 Issue/PR·case note는 그대로 보존된다.

## 만들 파일·변경

- `linemedic/control_plane/runs.py` 확장
  - `run_new()`: 새 run_id(D50), 이전 run `active=0`, run manifest(`config_hash`, host manifest 경로·hash, model·runtime·policy·prompt·contract hash, memory mode·snapshot), `routing_scope=eval:<run_id>`, setup credential로 `baseline/<run_id>`를 `BASELINE_COMMIT`에 생성(G2)
  - `archive(run_id)`: 새 intake·dispatch 정지 플래그 → execution UNKNOWN·notification SENDING/UNKNOWN 목록 확인(reconcile 안내 또는 미해결로 export에 포함) → export → 해당 run 라벨의 container·workspace만 정리
  - `export(run_id)`: `runs/<run_id>/export/`에 audit_events JSONL, manifest, 정제된 evidence·proposal·검사·verification·notification·case, run-record. 원본을 덮어쓰지 않는다. 공유본과 비공개 원본 구분
  - `reset(run_id)`: archive 후 `run_new` 안내(DB 삭제 없음)
- `ops_api.py` — `POST /ops/runs`, `POST /ops/runs/{id}/archive`
- CLI·Makefile — `make run-new`(완성), `make export-run RUN_ID=`, `make reset RUN_ID=`
- 테스트: `integration/test_reset_archive.py`

## 수용 기준

- T-RESET-01: reset 뒤 이전 run의 DB 행·evidence·case note가 남아 있고, FakeGitHub의 main·baseline·autofix 브랜치·PR·Issue가 그대로다. 새 run의 cold_start 검색은 과거 사례를 반환하지 않는다.
- 정리 대상은 run ID 라벨이 붙은 container·경로뿐이다(`docker ps --filter label=...`, 경로 prefix 검사). `prune`·wildcard 삭제 호출이 코드에 없다.
- 새 run에서 과거 알림을 다른 Issue로 재전송하지 않는다. 실패 알림의 재전송은 원래 route·Issue·logical key로만.
- live(G2): 실제 `baseline/<run_id>` 브랜치가 생성되고 보호 규칙이 적용되는지 확인.

## 금지·함정

- DB 전체 삭제, force push, 원격 main 되돌리기, 다른 리소스 prune을 하지 않는다.
- run_id에 `/`를 넣지 않는다(보호 패턴 불일치).
