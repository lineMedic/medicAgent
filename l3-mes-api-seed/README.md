# l3-mes-api

합성 공장 L3 라인의 불량 집계 API다. 실제 공장·고객 데이터가 아니라 합성 데이터만 쓴다.

## API

- `GET /defects/summary?lot_id=<로트 ID>` → `{"lot_id": ..., "total_defects": ..., "by_inspector": {...}}`
- `GET /healthz` → `{"status": "ok"}`

없는 로트는 404, 형식이 잘못된 로트 ID는 400이다.

## 업무 규칙

- 불량 record 하나가 불량 1건이다. `total_defects`는 로트의 전체 record 수다.
- `by_inspector`는 검사자 ID별 불량 건수다.
- 검사자 ID(`inspector_id`)가 없는 record도 전체 건수에서 빠지지 않으며, `미지정`으로 집계한다.
- 빈 로트는 `total_defects: 0`, `by_inspector: {}`다.

## 데이터

로트 파일은 `MES_DATA_DIR/lots/<lot_id>.json`에 있다(기본값은 이 저장소의 `data/`).
형식: `{"lot_id": "...", "records": [{"defect_id": "...", "inspector_id": "..."}]}`

## 로그

요청마다 JSON 한 줄을 stdout에 남긴다. 처리되지 않은 예외는 HTTP 500으로 응답하고 예외 종류·필드·위치를 로그에 남긴다.

## 테스트

- `tests/regression/`: 보호된 회귀 시험. 바꾸지 않는다.
- `tests/repro/`: 새로 발견한 문제의 재현 시험을 두는 위치.
