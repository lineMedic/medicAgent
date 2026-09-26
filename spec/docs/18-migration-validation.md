# 18. v3 → v4 전환·문서 검증·호환성

> **개발 전환 계획.** 실제 v3 제품 DB/소스가 제공되지 않았으므로 migration을 실행하지 않았다. fresh DDL 형식 확인과 제품 데이터 전환은 다른 일이다.

## 1. 의도적인 변경

| 영역 | v3 | v4 |
|---|---|---|
| 문서/메시지 | 문서 v3, wire `linemedic.v2` | wire `linemedic.v4`, mixed client 거부 |
| 조사 시작 | incident → dispatcher | Issue binding → work → 시작 알림 receipt → attempt |
| agent 제안 | run/incident/attempt | **work_id 추가**, 나머지 action 3개 유지 |
| 중복 구분 | run 포함 fingerprint·unique | stable problem fingerprint + routing_scope + work generation |
| 로그·Issue 입력 | 로그만 | 승인된 새 Issue와 로그, 단일 선점 |
| 결과 | 로컬 감사·PR·검사 | 실제 채널 알림 + 검증 수준별 사례 |
| 지식 | 정적 매뉴얼·런북 | 정적 자료 + ACL/snapshot 기반 사례 검색 |
| 검증 모드 | 과거 정답 미제공 | cold_start 엄격 유지, memory_assisted 별도 |

`linemedic.case.v4`는 case 문서 형식이며 도구 API의 `linemedic.v4`와 구분한다. workflow 상태의 권한 의미를 보존하며 Issue closed를 legacy RESOLVED로 import하지 않는다.

## 2. 이미 코드/DB가 있다면

1. 새 issue intake·dispatch·외부 쓰기를 멈추고 현재 RUNNING/UNKNOWN execution과 notification을 기록한다. 불명확한 실행을 취소로 추정하지 않는다.
2. 일관된 SQLite backup과 run artifact를 보존한다. WAL이 있을 수 있으므로 DB 파일 하나만 임의 복사하는 방식으로 원본 보존을 보장했다고 하지 않는다.
3. schema와 실제 구현 필드를 점검해 versioned migration을 별도로 작성한다. **04의 CREATE TABLE을 기존 DB에 그대로 실행하지 않는다.**
4. 기존 run은 inactive history로 남긴다. 당시 verification identity·source 근거를 확인할 수 있는 것만 그 수준으로 import한다. 부족한 것은 UNVERIFIED/INCONCLUSIVE다.
5. repo·service·fingerprint 정규화를 재구성한다. 과거 run 포함 hash에서 안정 signature를 되찾을 수 없으면 임의 값을 만들어 대응시키지 않고 legacy 키로 보존한다.
6. 진행 중인 기존 PR은 운영자가 실제 Issue·work에 연결하고 현재 head·candidate·승인 상태를 확인한다. 없는 과거 start receipt를 소급 생성해 ‘미리 알렸다’고 하지 않는다. `origin=legacy_import`와 제한을 기록한다.
7. agent client와 server를 같이 v4로 전환하고 schema reject·scope·idempotency·DDL 제약을 시험한다. 운영 action을 자동 재실행하지 않는다.
8. Issue mirror 최초 동기화는 observe_only로 하고 활성화 기준시각을 저장한다. 기존 backlog는 로그 binding/운영자 승인 없는 자동 작업에서 제외한다.
9. 선택 알림 채널 한 개를 팀 소유 테스트 Issue/수신자에서 확인한다. 공개·제3자 recipient에게 시험 메시지를 보내지 않는다.
10. shadow mode에서 match/work 계획만 검토한 뒤 operator가 write-enabled를 명시적으로 켠다. 이후 actual agent 경로를 평가한다.

## 3. 되돌리는 경우

오류가 나면 신규 접수·쓰기 중단 후 v4 원본·UNKNOWN을 보존한다. 원격 Issue/댓글/PR은 이미 발생한 부작용이므로 DB rollback만으로 되돌아가지 않는다. 기존 v3 프로그램을 다시 띄우더라도 미해결 v4 외부 작업을 자동 생성/재실행하지 않도록 정리·명시적 승인한다.

이것은 자동 무중단 업그레이드 보장이 아니다. 현재 v3 코드가 없다면 fresh v4 개발로 시작하고 이전 데이터 migration 완료라고 말하지 않는다.

## 4. 문서 검증 기준

패키지 생성 시 검증할 범위:

- 상대 Markdown 링크 대상 파일·anchor 존재, fence 짝, JSON/YAML 코드블록 구문.
- 04의 fresh SQLite DDL을 메모리 DB에서 실행, FK와 unique/check 제약의 일부 부정 시험.
- 선택 FTS5 table과 간단한 검색이 **문서 작성 환경**에서 동작하는지 확인.
- wire version, work/incident/action/outcome, core로 승격된 H01/H02와 이전 P1 Issue/알림 문구의 충돌 점검.
- v3 원본 hash와 수정 파일 목록·ZIP 무결성.

이 검사는 실제 GitHub search/댓글/작성자 권한·SMTP 수신·polling·NVIDIA SDK·sandbox·모델·릴리스·업무 E2E를 실행하지 않는다. 서버의 도메인 검사가 아직 없으므로 모든 상태 불변식이 강제된다는 의미도 아니다.

실제 결과는 [PACKAGE-VALIDATION](../PACKAGE-VALIDATION.md)에 적는다. 예정된 시험은 PASS로 표시하지 않는다.

## 5. 구현 완료의 최종 확인

완료 여부는 [01](01-requirements.md)의 FR·AC, [09](09-scenarios-evaluation.md)의 actual 테스트, [실행 기록](../templates/run-record.md)으로 판단한다. 설계 문서가 더 완성됐다는 것과 제품이 구현됐다는 것을 분리한다.
