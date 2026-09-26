# W04 — 합성 MES 서비스(버그 base)·fixture·holdout·scenario-s1

| 항목 | 값 |
|---|---|
| 등급 | core |
| 자율성 | A (컨테이너 실행 테스트는 `docker` 마커) |
| 선행 | B00 |
| 목표 상태 | UNIT_TESTED |
| 원본 근거 | [spec 09 §1·§2](../spec/docs/09-scenarios-evaluation.md), [spec 08 §4](../spec/docs/08-release-verification.md), [spec 05 §6 code-exception](../spec/docs/05-agent-spec.md), [spec 07 §5](../spec/docs/07-security.md) |
| 참조 | [docs/02 §3](../docs/02-repo-layout.md), [docs/07 §4](../docs/07-constants.md), DECISIONS D42·D57 |
| 요구 | AC-S1 준비, AC-S1B 준비 |

## 목표

`l3-mes-api-seed/`의 MES가 `GET /defects/summary?lot_id=L3-0927-118`에서 실제로 `KeyError`를 내고 JSON 오류 로그를 남기며, 정상 로트 101은 맞는 집계를 돌려준다. holdout 기대값은 `linemedic/eval/`에만 있다.

## 만들 파일

- `l3-mes-api-seed/app/main.py` — FastAPI: `GET /defects/summary?lot_id=`(200 JSON `{lot_id, total_defects, by_inspector}`), `GET /healthz`. 처리되지 않은 예외는 500 + JSON 오류 로그
- `l3-mes-api-seed/app/defects.py` — `summarize(lot_id, records)`. **버그 base**: `row['inspector_id']` 직접 접근
- `l3-mes-api-seed/app/data.py` — `MES_DATA_DIR/lots/<lot_id>.json` 로더(없는 로트는 404)
- `l3-mes-api-seed/app/logging_json.py` — D57 필드의 JSON Lines를 stdout으로. 예외 시 `error_type`, `error_field`(KeyError의 키), `top_frame`(`module:function`, 줄 번호는 별도 필드), `stack`
- `l3-mes-api-seed/data/lots/L3-0927-118.json`, `L3-0927-101.json` — docs/07 §4 값. 키 없는 record는 필드를 생략. 각 record에 고유 `defect_id`
- `l3-mes-api-seed/tests/regression/test_summary_regression.py` — 보호 회귀: 로트 101 정확 집계, 빈 배열 로트 → `0`·`{}`. **버그 base에서 통과**해야 한다(118은 여기서 시험하지 않음)
- `l3-mes-api-seed/tests/repro/.gitkeep`, `l3-mes-api-seed/README.md` — 업무 규칙(누락 검사자는 `미지정`, 전체 건수 유지)만 설명. 정답 코드 없음
- `linemedic/eval/holdout-defects-v1.json` — L3-HOLDOUT-201 입력 record와 기대값(I-03=2, 미지정=3, 총 5)
- `linemedic/factory_sim/fixtures/` — 테스트·시나리오가 쓰는 공개 로트 사본(또는 seed 경로 참조)과 빈 로트
- `linemedic/runner/mes.Dockerfile` — 신뢰 레시피: 고정 python base image(digest 또는 태그+기록), 고정 버전 의존성 설치, `app/`만 복사. repo 안의 Dockerfile·setup script를 쓰지 않는다. 데이터는 실행 시 read-only mount
- `linemedic/factory_sim/scenarios.py` — `inject_s1(run_id)`: 버그 base image로 MES 컨테이너 기동(라벨 `linemedic.run_id`, 내부 network), 로트 118 요청 3회 이상(60초 안)과 로트 101 요청을 보냄. 배포 기록은 W07 이후 `DEPLOY_OBSERVED`로 남긴다
- `linemedic/scripts/seed_demo_repo.py` — 시드 디렉터리에서 결정적인 git 이력(작성자·시각 고정)으로 버그 base 커밋을 만든다(`RUNS_DIR/seed-repo/`). push는 W03에서
- CLI·Makefile: `make scenario-s1 RUN_ID=`, `make mes-image`
- 테스트: `linemedic/tests/unit/test_mes_seed.py`, `linemedic/tests/integration/test_mes_container.py`(docker)

## 구현 단계

1. fixture JSON을 만들고 무결성 테스트를 쓴다: 개수(7·3·5), 키 없는 record 수(2·0·3), `defect_id` 고유, 문자열 `"key 없음"`이 없음.
2. 시드 앱을 작성하고 FastAPI TestClient로 테스트한다: 118 → 500 + 로그의 `error_type=KeyError`, `error_field=inspector_id`; 101 → 정확 집계; 빈 로트 → 0.
3. 보호 회귀 테스트가 버그 base에서 통과하는지 확인한다(시드의 venv 없이 linemedic 테스트에서 subprocess로 pytest 실행 가능).
4. `seed_demo_repo.py`로 git 이력을 만들고, 두 번 실행해도 같은 tree hash가 나오는지 테스트한다.
5. `mes.Dockerfile`과 `make mes-image`를 만들고 docker 마커 테스트로 컨테이너 기동·로그 확인.
6. `inject_s1`과 `make scenario-s1`을 만든다(감지 연결은 W07).

## 수용 기준

- 118 요청이 버그 base에서 실패하고, 로그 한 줄이 D57 필드를 모두 가진다.
- 보호 회귀가 버그 base에서 통과한다.
- `l3-mes-api-seed/` 어디에도 수정된 집계 코드·holdout 기대값·시나리오 이름(`S1`, `expected_category`)이 없다(테스트로 grep).
- 시드 git tree hash가 결정적이다.

## 금지·함정

- 정답 패치를 시드·fixture 주석·README에 넣지 않는다. 정답 diff가 필요한 테스트용 파일은 `linemedic/tests/fixtures/` 또는 `linemedic/eval/manual_proposals/`에만 둔다.
- `null`·빈 문자열 inspector를 fixture에 넣어 정책을 넓히지 않는다.
- MES 로그에 비밀·환경변수를 찍지 않는다.
