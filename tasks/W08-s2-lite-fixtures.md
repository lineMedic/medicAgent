# W08 — S2-lite 카메라 지표·설비·매뉴얼·혼동 사례

| 항목 | 값 |
|---|---|
| 등급 | core |
| 자율성 | A |
| 선행 | W06, W07 |
| 목표 상태 | UNIT_TESTED |
| 원본 근거 | [spec 09 §3](../spec/docs/09-scenarios-evaluation.md), [spec 03 §2·§3.B](../spec/docs/03-api-contracts.md), [spec 05 §6 vision-quality-drop](../spec/docs/05-agent-spec.md), [spec 06 §7](../spec/docs/06-broker-runner.md) |
| 참조 | [docs/07 §4](../docs/07-constants.md), DECISIONS D58·D59 |
| 요구 | FR-09, AC-S2 준비, INV-10 |

## 목표

`make scenario-s2-lite RUN_ID=`가 L3-CAM-2 이상을 만들어 `vision-inspection` incident가 생기고, 에이전트가 `query_equipment_metrics`·`get_knowledge`·`get_deploys`로 비교 근거를 조회할 수 있다. `--recent-deploy` 변형은 원인이 아닌 최근 배포 기록을 추가한다.

## 만들 파일

- `config/linemedic.toml` — `services.vision-inspection`(line L3, repository는 mes-api와 같음, `code_paths = []`), `equipment`(L3-CAM-1~3)
- `linemedic/factory_sim/camera_metrics.py` — run별 합성 시계열(30분, 60 sample). [docs/07 §4](../docs/07-constants.md) 표 값. 이상은 CAM-2에만
- `linemedic/factory_sim/manuals/MANUAL-L3-VISION-4.2.md` — **팀이 만든 가상 매뉴얼**. 첫 줄에 "실제 산업 매뉴얼·안전 절차가 아님"을 적는다. 절 ID를 가진 짧은 점검 요청 안내만
- `linemedic/policies/manual_templates.toml` (D60) — 정비 초안에 브로커가 채울 승인 안내 문구(manual_ref_id별)
- `linemedic/control_plane/tools_api.py` 추가 — `query_equipment_metrics`(등록 설비만, 최대 30분·60 sample, baseline·품질 정보), `get_knowledge`(허용 매뉴얼·런북 절만 부분 문자열 검색, URL fetch 없음)
- `linemedic/control_plane/detector.py` 추가 — 지표 이상 규칙(예: baseline 대비 brightness −30% 이하 또는 confidence < 0.7 이 연속 3 sample) → incident(service `vision-inspection`, `endpoint_or_metric=metric:L3-CAM-2:brightness_drop`)
- `linemedic/factory_sim/scenarios.py` 추가 — `inject_s2_lite(run_id, recent_deploy=False)`
- CLI·Makefile: `make scenario-s2-lite RUN_ID= [RECENT_DEPLOY=1]`
- 테스트: `unit/test_camera_metrics.py`, `integration/test_s2_tools.py`

## 수용 기준

- CAM-2 observed brightness 59, confidence 0.61, CAM-1·3 정상값이 표와 같다.
- 등록되지 않은 `equipment_id` → 404, 다른 incident 범위 → 거부.
- `get_knowledge`가 매뉴얼 밖 경로·URL을 반환하지 않는다.
- recent-deploy 변형에서 `get_deploys`에 24시간 안의 mes-api 배포가 보인다. 기본 변형에서는 없다.
- 도구 응답·매뉴얼 어디에도 "렌즈 오염이 원인" 같은 확정 문구나 시나리오 정답이 없다.

## 금지·함정

- 실제 영상 인식·historian 서버를 만들지 않는다. 시뮬레이션 모듈이면 충분하다.
- 매뉴얼에 실제 설비 조작 절차·제어값을 쓰지 않는다.
- 지표 숫자만으로 원인을 확정하는 문장을 fixture에 넣지 않는다.
