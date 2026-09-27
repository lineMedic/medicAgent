# factory_sim fixtures

- 공개 로트 입력(L3-0927-118, L3-0927-101)은 사본을 두지 않고 `l3-mes-api-seed/data/lots/`를 그대로 참조한다. 두 곳의 값이 어긋나지 않게 하기 위해서다.
- `lots/L3-EMPTY-000.json`: 빈 로트. 정상 집계는 `total_defects: 0`, `by_inspector: {}`다.
- holdout 입력·기대값은 여기에 두지 않는다. `linemedic/eval/holdout-defects-v1.json`에만 있다.
