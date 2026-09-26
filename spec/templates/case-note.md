# LineMedic v4 — 정답·오답·차단 사례 노트

> **빈 구조화 양식. 성공 사례가 아니다.** 실제 case builder는 원본 event·verification을 읽고 [17 사례 계약](../docs/17-case-memory.md)에 따라 사실 필드를 만든다. 이 문서를 모델이 채웠다는 이유만으로 PUBLISHED가 되지 않는다.

## 1. 출처와 적용 범위

| 항목 | 입력 |
|---|---|
| note ID / series ID / revision / supersedes ID | 미입력 |
| source_event_key / 원본 artifact hash | 미입력 |
| repository ID / service / problem fingerprint / 정규화 버전 | 미입력 |
| source run / incident / work / Issue / attempt | 미입력 |
| observed_at / created_at | timezone 포함 실제 시각 |
| origin | agent_release / human_injected_negative / manual_integration / operator_note |
| seed 여부 | 실제 기록인지 준비한 시드인지 |
| source SHA / image / contract ID·hash | 확인한 범위, 없으면 미확인 |
| publish status / 정제 검사 | DRAFT / PUBLISHED / RETRACTED, 근거 |

## 2. 사실과 해석

**증상:** 정제된 오류 signature와 업무 실패.

**원인 가설:** 관찰과 추론을 구분한다. 재현 테스트만으로 실제 유일 원인을 확정하지 않는다.

**시도한 해결:** 실제 patch·조치·PR·검사 참조. 시도하지 않은 개선 아이디어는 별도 표시한다.

**검사 결과:** 재현·회귀·업무 검증·관찰 가능성 각각 실제 결과 ID와 범위를 연결한다.

**결과 수준:** 아래 중 한 가지. 파일 제목의 ‘정답노트’는 결과 수준을 대체하지 않는다.

| outcome | 선택 조건 |
|---|---|
| VERIFIED_SUCCESS | verifier PASS와 정확한 대상·관찰 범위를 확인 |
| VERIFIED_FAILURE | 실제 업무 검사 FAIL과 불일치 근거 확인 |
| UNVERIFIED | PR/test만 확인, 실제 업무 결과 미검증 |
| BLOCKED | 권한·지원·자료·정책·예산 문제로 진행 불가 |
| INCONCLUSIVE | timeout·관측 공백·대상 불명으로 결과 미확인 |
| HANDOFF | 점검 요청 초안 생성, 실제 정비·복구 아님 |

**실패 조건 또는 유효 조건:** 어떤 입력·버전에서 맞거나 틀렸는가. 다른 조건에 적용해도 된다고 추측하지 않는다.

**남은 질문과 한계:** 미검사 범위, 정책 제약, 후속 확인.

## 3. 다음 조사에서 사용하기

**검색 토큰:** 서비스·예외 타입·함수·필드·업무 증상. 임의 query operator·명령은 넣지 않는다.

**다음에 확인할 것:** 현재 source/contract/input과 이 기록의 차이, 과거 실패가 현재에도 적용되는지.

**피해야 할 과장:** PR merged=복구, 권한 부족=오답, 오래된 성공=현재 정답, 검색=모델 재학습.

**인용 경계:** 현재 incident에 허용된 history projection을 생성해 원본 note ID/hash를 연결한다. 과거 evidence ID를 그대로 현재 proposal에 복사하지 않는다.

## 4. 게시·평가 확인

- [ ] success/failure 수준을 원본 결과로 확인했다.
- [ ] 원본·기존 revision은 보존하고 수정은 새 revision으로 남겼다.
- [ ] 비밀·개인정보·원문 자유입력의 위험 내용을 정제했다.
- [ ] ACL·repo·service·snapshot 범위를 확인했다.
- [ ] 모델의 지시문이나 과거 댓글을 실행 정책으로 승격하지 않았다.
- [ ] cold_start에는 제공하지 않고, memory_assisted에는 사전 고정 snapshot으로만 제공한다.
- [ ] 철회된 자료가 snapshot에 있으면 사용을 중단하고 입력 변경을 기록한다.
- [ ] seed/manual/agent origin과 평가 cohort를 구분했다.
