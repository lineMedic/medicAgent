# W21 — README·영상·주장 점검·전원 개별 제출

| 항목 | 값 |
|---|---|
| 등급 | core |
| 자율성 | H — 에이전트는 README·신청서 초안과 주장 점검만. 녹화·제출은 사람 |
| 선행 | W20, W29 (W01의 R1~R5 상태) |
| 목표 상태 | LIVE_VERIFIED (사람이 제출을 마쳤다고 기록) |
| 원본 근거 | [spec 13 전체](../spec/docs/13-demo-submission.md), [spec 12 §7](../spec/docs/12-nvidia-requirements.md), [spec 11 §8](../spec/docs/11-runbook.md), [spec 07 §8](../spec/docs/07-security.md) |
| 참조 | [docs/11 §4·§6](../docs/11-definition-of-done.md) |
| 요구 | FR-14 |

## 목표

제출물(README, 영상, 신청서)이 **실제로 실행하고 증거가 있는 것만** 주장한다.

## 에이전트가 할 일

1. 루트 `README.md`를 제출용으로 다시 쓴다(지금의 "문서 패키지 안내"는 끝부분 한 절로 줄여 남긴다). 내용: 제품 정의, 실제 구현 범위와 미구현 항목, 재현 방법(실제 사용한 OS·버전·image ID·lockfile·설정 template, **성공한 명령의 실제 출력**), API 키·리뷰어 없이 가능한 단계와 외부 서비스가 필요한 단계 구분, 실제 사용한 NVIDIA 기술과 버전, 평가 결과 표(W20·W29 분모 포함), 합성 환경임을 명시.
2. 신청서 초안: [spec 13 §5](../spec/docs/13-demo-submission.md) 문안에서 **구현되지 않은 문장을 삭제**한다. Tech Stack에는 실제 모델 ID·endpoint·runtime·OpenShell 정책·버전만. NAT·Guardrails는 실제 경로에서 쓴 경우만.
3. 영상 구성표: [spec 13 §4](../spec/docs/13-demo-submission.md)의 3분 구성을 실제 녹화 가능한 장면으로 조정. 생략한 사람 승인·대기 시간을 무인 즉시 처리처럼 보이게 하지 않는다. 여러 run을 한 live run처럼 편집하지 않는다.
4. 점검 목록 실행(아래)과 결과 보고.

## 제출 전 점검 ([spec 12 §7](../spec/docs/12-nvidia-requirements.md), [spec 13 §8](../spec/docs/13-demo-submission.md))

- [ ] 모델이 만든 산출물과 사람의 산출물이 구분돼 있다.
- [ ] 실제 runtime과 sandbox 통합 수준이 그림·문구·영상에 일치한다.
- [ ] Skill API·NeMo 조건(R1·R2)의 근거 또는 미확인 상태가 남아 있다.
- [ ] cloud API 데모를 폐쇄망 실행이라고 쓰지 않았다.
- [ ] GPU·크레딧·상용 라이선스를 확인 없이 전제하지 않았다.
- [ ] 쓰지 않은 기술을 "사용"으로 적지 않았다(예정은 예정으로).
- [ ] S1/S2-lite/S1b/S3/S4~S7 중 실제 완료·미실행을 구분했다.
- [ ] Issue 확인·시작 receipt·attempt 시각·PR·검사 identity가 연결된다.
- [ ] 댓글·메일 접수와 사람 수신·열람을 혼동하지 않았다.
- [ ] case origin·snapshot·검색 엔진·cold/memory 분모를 명시했다.
- [ ] 사람 패치·승인·배포·S1b를 무인 agent 성과로 합산하지 않았다.
- [ ] 회사·고객 데이터 없이 합성 환경임을 밝혔다.
- [ ] R1~R5 원문·답변 또는 미확인 상태를 보존했다.
- [ ] 영상 길이·저장소 공개/심사자 접근·팀원 개별 제출을 확정 조건(R4·R5)에 맞췄다.
- [ ] [docs/11 §4](../docs/11-definition-of-done.md)의 금지 표현이 없다.

## 사람이 할 일

영상 녹화·편집, 저장소 공개 범위 설정, 신청서 입력·**팀원 전원 개별 제출**, 제출 시각 기록.

## 금지·함정

- 에이전트가 신청서를 제출하거나 영상을 게시하지 않는다.
- 서비스 장애로 live 시연이 안 되면 녹화된 영상·trace를 쓸 수 있지만 "당시 실시간 실행"으로 표시하지 않는다.
