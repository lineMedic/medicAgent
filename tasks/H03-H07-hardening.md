# H03~H07 — hardening (core 완료 후에만)

| 항목 | 값 |
|---|---|
| 등급 | hardening |
| 자율성 | A/C |
| 선행 | **core 전체 경로가 실제로 통과한 뒤** (V4-CP5 증거 확보 후) |
| 목표 상태 | UNIT_TESTED (미완료 시 "설계됨/미구현"으로 공개) |
| 원본 근거 | [spec 01 §2 hardening 요구](../spec/docs/01-requirements.md), [spec 10 §5](../spec/docs/10-delivery-plan.md), [spec 08 §1·§5](../spec/docs/08-release-verification.md), [spec 06 §6](../spec/docs/06-broker-runner.md) |
| 요구 | HR-03~HR-07 |

H01(claim 경합)과 H02(같은 키·다른 body 409)는 v4에서 **core로 승격**돼 W25·W06에 있다. 여기서 다시 만들지 않는다.

| ID | 요구 | 내용 | 할 일 | 테스트 | 미완료 시 공개 문구 |
|---|---|---|---|---|---|
| H03 | HR-03 | 로그 heartbeat·cursor로 관찰 공백 탐지 | verifier 관찰 구간에 주기적 synthetic 업무 요청을 넣고 그 로그 heartbeat와 cursor 연속성을 확인 | T-VERIFY-03 | "core의 '스트림 읽기 성공 + container 불변' 기준만 적용" |
| H04 | HR-04 | 결과 불명 실행의 bounded 자동 재조회 | execution·notification UNKNOWN을 제한 횟수·간격으로 자동 reconcile. 새 변경은 여전히 안 함 | T-EXEC-02 | "사람이 `make reconcile`로 확인하는 절차만 있음" |
| H05 | HR-05 | 빌드 레시피 hash를 identity chain에 포함 | `build_recipe_sha256`(mes.Dockerfile·의존성 lock) 기록 | release 기록 테스트 | "레시피 파일 경로·커밋만 기록" |
| H06 | HR-06 | 테스트 카탈로그의 hardening 항목 전부 | 운영 조합 확장 시험(09 §6의 hardening 행 외 추가 장애 조합) | 추가 ID | "실행한 테스트 ID만 보고" |
| H07 | HR-07 | 화면 확장 | 상세 타임라인·필터·run 비교 화면 | UI 테스트 | "core 화면만 공개" |

## 규칙

- core가 하나라도 미완료면 hardening에 착수하지 않는다. 미완료 core를 hardening으로 재분류해 완료를 주장하지 않는다.
- hardening을 넣으며 core 계약(API·DDL·상태)을 바꾸면 docs·tests·카드를 같은 커밋에서 고치고 DECISIONS.md에 기록한다.
