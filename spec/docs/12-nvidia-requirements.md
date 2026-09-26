# 12. NVIDIA 스택·참가 조건·기술 확인표

> v4: v3의 기술 선택·정직성 경계를 유지하고 Issue·알림·이력의 실제 연동 확인을 추가했다. 현재 남은 시간의 체크포인트는 [10](10-delivery-plan.md)을 따른다. 기술의 공식 기능, 팀 환경에서의 실제 작동, 대회에서의 인정은 서로 다른 사실이다. 이 문서는 세 가지를 분리한다. **현재 모든 환경 확인 결과는 미입력·미검증**이다.

## 1. 대회 조건의 현재 지위

원안 §2·§16에 있는 조건과 사용자가 제공한 트랙 스크린샷을 계승한다. 이 패키지를 작성하면서 주최 측의 새 답변이나 로그인 신청서 내용을 확보한 것은 아니다.

| ID | 원안 또는 제공 자료의 내용 | 지금 필요한 확인 | 현재 상태 |
|---|---|---|---|
| R1 | build.nvidia.com의 ‘Skill API’를 활용 | 정확히 어떤 API·공식 Skill·사용 증거가 인정되는가 | UNCONFIRMED |
| R2 | 스크린샷: **NeMo Framework 또는 NeMo Microservices 활용** | 필수 여부, NAT·OSS Guardrails와의 관계, 제공 환경 | UNCONFIRMED |
| R3 | 활용 심도 / 산업가치·혁신 / 완성도 / 독창성 | 최종 원문과 배점·필수 조건의 구분 | 제공 자료 기준, 최종 원문 확인 필요 |
| R4 | 데모·코드·개별 신청 | 영상 길이, 저장소 공개/초대, 모든 문항, 팀원별 제출 | UNCONFIRMED |
| R5 | 원안의 9/28 23:59 및 다른 날짜 표기 언급 | 공식 마감·시차, 내부 제출 시점 | 원안의 더 이른 일정을 보수적으로 사용하되 확정 아님 |

‘NAT를 사용했으니 R2 해결’, ‘OpenClaw의 SKILL.md가 있으니 R1 충족’으로 체크하지 않는다. 기술 담당자의 해석과 주최 측의 인정은 구분한다.

## 2. 주최 측에 확인할 문안

아래는 **보낼 문안**이다. 이 문서 생성으로 실제 문의가 발송되지는 않는다.

> 저희는 Nemotron 기반 공장 IT 장애 대응 에이전트를 개발하고 있습니다. 제출 조건을 정확히 맞추기 위해 세 가지를 확인 부탁드립니다.
>
> 1. ‘build.nvidia.com의 Skill API 활용’에서 인정하는 구체적인 API 또는 공식 Skill은 무엇이며, 어떤 실행 증거가 필요한가요?
> 2. Creative Use-case 트랙의 ‘NeMo Framework 또는 NeMo Microservices 활용’은 필수 조건인가요? Nemotron NIM endpoint + NeMo Agent Toolkit, 또는 OSS NeMo Guardrails API 서버 구성은 인정되나요? 공식 NeMo Microservices가 필수라면 참가자가 사용할 수 있는 제공 환경·접근 경로가 있나요?
> 3. 온라인 예선의 최종 마감 시각, 영상 길이, 저장소 공개/심사자 접근 방식과 팀원 개별 제출 여부를 확인 부탁드립니다.

답변은 `확인일 / 질문 원문 / 답변 원문 / 출처 / 확인자 / 영향받는 작업`으로 보존한다. 답변이 오지 않은 조건을 ‘확인 완료’로 바꾸지 않는다.

## 3. 기술별 사용 계획과 증명 범위

| 기술 | 역할 | 사용했다고 표시할 최소 증거 | 자동으로 따라오지 않는 주장 |
|---|---|---|---|
| Nemotron | 실제 증거 조사·패치·제안 생성 | 모델 ID·provider request·tool trace·원본 제안 | 모든 버그 수정 가능, 코드/설비 원인 판별 보장 |
| NemoClaw / OpenClaw | 원안 유지 후보 런타임 | 실제 session·도구·로컬 파일/test·제안 왕복 | 교육 수강만으로 제품 통합 완료 |
| NeMo Agent Toolkit | 주 런타임 실패 시 단일 대안, 실제 선택 시 도구 연결·관찰·평가 | 선택한 버전·실행 설정·해당 run의 trace | R2 자동 충족, 사용하지 않은 경로까지 적용 |
| OpenShell | 에이전트 실행 경계 | effective policy·sandbox identity·정상/금지 접근 대조·거절 기록 | 샌드박스 탈출 불가, 생성 코드까지 자동으로 안전 |
| NeMo Guardrails | 요구 조건과 일정이 허용할 때 한 가지 명시적 보호 기능 | 실제 선택한 배포 형태·설정·정상/공격 시험 | OSS 서버를 공식 Microservice라고 부를 수 있음 |
| 공장 내 NIM | 온프레미스 추론의 후속 설계 | 실제 배포 전까지 ‘계획’만 표시 | endpoint 변경만으로 운영 이행 검증 완료 |
| Embedding / Retriever | P1 이후 검토. core의 사례 검색은 별도 SQLite lexical 경로 | 실제 검색과 인용 결과 | 설치만으로 제품 활용 심도 확보 |

### 3.1 심사 1번 항목("NVIDIA Agent 기술 활용 심도")에 낼 증거 `[core]`

무결성 설계는 3분 영상에서 잘 보이지 않는다. 아래 증거는 **화면·영상·README에서 직접 보이도록** 준비한다.

| 증거 | 어디서 보여주나 | 담당 |
|---|---|---|
| 샌드박스 안에서 실행 중인 에이전트 (agent_mode=sandbox, 샌드박스 identity) | 화면 실행 헤더, 영상 0:20~1:15 | B+D |
| 모델이 고른 도구 호출 순서와 결과 (tool trace) | 화면 타임라인 | B |
| OpenShell 정책 파일·hash와 금지 요청 거절 로그 | 영상 S3 구간, README | D |
| (NAT 선택 시) NAT trace·지연·토큰 | 화면 타임라인 또는 별도 표 | B |
| (R2 필수 확인 시) Guardrails 정상 통과·공격 차단 시험 | README, 영상 | B+D |
| 사용한 모델 ID·런타임·OpenShell 버전 | 신청서 Tech Stack, README | D |

공식 NAT 문서는 기존 프레임워크와 함께 쓰는 도구 연결, 프로파일링·관찰·평가 기능을 설명한다. 정확한 설치 버전과 API는 팀이 선택한 릴리스에서 확인한다. [W01](14-decisions-sources.md#w01)

공식 Guardrails 문서는 Library API Server와 NeMo Guardrails Microservice를 구분한다. 라이브러리를 직접 API로 제공하는 것을 공식 Microservice 배포와 동일시하지 않는다. 이 구분 자체가 주최 측의 인정 답변을 대신하지는 않는다. [W09](14-decisions-sources.md#w09)

## 4. 런타임 선택: 하나만 구현한다

**원안의 NemoClaw/OpenClaw를 우선 후보로 유지한다.** 현재 [10](10-delivery-plan.md)의 V4-CP0에서 기존 최소 왕복의 실측을 확인한다. 실패하면 NAT를 대안으로 **한 번 전환**한다. 동일한 HTTP 도구 계약을 사용하므로 Control Plane을 다시 만들지 않는다.

선택 조건은 제품 실행에 필요한 다음 네 가지다.

1. 실제 모델이 도구를 선택하고 결과를 다시 읽는다.
2. 지정한 repo 사본을 읽고, 허용된 로컬 파일을 수정하고, 사전 설치된 test를 실행한다.
3. 유효 제안을 `/tools/proposals`에 제출하고 결과를 조회한다.
4. host 측에서 deadline·실행 종료·trace 회수를 관리할 수 있다.

이때 로컬 프로세스로 성공한 것과 OpenShell 안에서 성공한 것을 별도 체크한다. NAT가 선택됐으면 실제 평가도 NAT로 수행한다. 설치만 해 두고 신청서에 두 런타임을 모두 사용했다고 쓰지 않는다.

[05의 `run_agent(...)`](05-agent-spec.md)는 **팀 내부 adapter 계약**이다. 교육 자료에 등장한 gateway 경로·SDK 메서드를 검증 없이 현재 API로 복사하지 않는다.

## 5. 기술 스파이크 기록

| ID | 담당 | 확인할 것 | 통과 증거 | 미달 시 |
|---|---|---|---|---|
| N01 | B | Nemotron 모델 접근·도구 호출 형식 | tool→결과 재입력→제안 성공 | 접근 가능한 Nemotron 후보 한 번 교체, 변경 기록 |
| N02 | B+D | runtime의 로컬 코드/test 지원 | pinned repo 사본에서 읽기·test 실행 | 단일 NAT adapter로 전환 |
| N03 | D | sandbox→Control API 접근·인가 | 허용 조회/제안, 관리 권한 거절 | 주소·effective policy·API auth를 각각 조사 |
| N04 | D | 실제 policy 문법·적용·거절 로그 | 고정된 policy 파일·hash·대조 결과 | 문서만 있는 상태로 구분, 차단 주장 보류 |
| N05 | B+D | NVIDIA 키·agent token 전달 위치 | 이름·권한·위치만 기록한 secret inventory | 추론 전용 키가 내부에 있으면 그대로 공개 범위 설명 |
| N06 | D | runner·배포 MES의 네트워크·마운트 | Docker inspect·접근 시험·자원 제한 | 외부 생성 코드 실행/배포 경로 중단 |
| N07 | C | 조직 저장소·봇 credential·`baseline/*` 패턴 보호·squash 단일 머지 | 봇 PR을 다른 팀원이 승인 가능, 리뷰 없는 머지·봇 직접 push 거절 | 보호 구현 확인 전 승인 배포 완료 주장 금지 |
| N08 | 전원 | OS·Python·패키지·image 호환 | 재현 환경 manifest | 원안 버전을 강제하지 말고 호환 버전 하나로 고정 |
| N09 | D | 데모 호스트에서 OpenShell·선택 런타임·Docker 조합 동작 (Support Matrix 대조) | 호스트 manifest, 샌드박스 기동 기록 | 다른 팀원 머신 또는 VM으로 호스트 교체. 평가 시작 전에만 교체 가능 |
| N10 | B+D | (OpenClaw 선택 시) 워크스페이스·스킬 파일의 에이전트 쓰기 가능 여부 | 쓰기 시도 결과 | 쓰기 가능하면 attempt 전후 해시 기록 ([05](05-agent-spec.md) 3절) |

R1/R2가 실제 필수이며 제공 환경이 없다면, 담당자가 주최 측과 인정 가능한 최소 통합을 확인한다. 필요한 기술을 단순히 그림에 추가하거나 API 이름만 바꿔 충족했다고 하지 않는다. 필수 조건 불충족은 기능 완성과 별도로 제출 리스크에 남긴다.

## 6. 모델·버전 기록

원안의 `nvidia/nemotron-3.5-lightning-30b-a3b`는 **원안에 적힌 후보명**이다. 이 패키지에서 접근 가능성이나 tool calling을 실측한 것이 아니다. 환경변수 `NVIDIA_MODEL_ID`에는 실제 선택한 모델 ID를 넣는다.

| 항목 | 현재 입력값 |
|---|---|
| runtime / version | NOT_TESTED |
| NVIDIA model ID / endpoint | NOT_TESTED |
| Python / OS / CPU architecture | NOT_TESTED |
| OpenShell version / policy hash | NOT_TESTED |
| runner image / dependency lock hash | NOT_TESTED |
| 실제 사용 run ID / trace 위치 | 없음 |
| R1 인정 근거 | UNCONFIRMED |
| R2 인정 근거 | UNCONFIRMED |

모델·prompt·policy·runtime을 바꾸면 실행 집합을 분리한다. 토큰 수가 관찰되지 않으면 `null`, 일부 호출만 있으면 `partial`로 기록한다. 확인되지 않은 단가로 비용을 계산하지 않는다.

## 7. 제출 직전 기술 주장 점검

- [ ] 모델이 만든 산출물과 사람의 산출물이 구분돼 있다.
- [ ] 실제 runtime과 sandbox 통합 수준이 그림·문구·영상에 일치한다.
- [ ] Skill API와 NeMo 조건의 근거 또는 미확인 상태가 남아 있다.
- [ ] cloud API 데모를 폐쇄망 실행 결과라고 표현하지 않는다.
- [ ] 제공 GPU·Brev 크레딧·상용 라이선스 이용 가능성을 확인 없이 전제하지 않았다.
- [ ] 사용하지 않은 기술을 ‘예정’이 아닌 ‘사용’으로 표기하지 않았다.


## 8. v4 추가 연동 스파이크

| ID | 담당 | 확인 | 미확인 시 |
|---|---|---|---|
| N11 | C | GitHub repo ID·Issues read/write·댓글·pagination·bot author | Issue 자동화 미완료 |
| N12 | D+C | selected notification route의 실제 접수·timeout·미전송 | 알림 완료 주장 금지, start gate 유지 |
| N13 | A+B | SQLite FTS5 지원·한국어/오류 토큰·ACL/snapshot 필터 | 명시한 keyword fallback 또는 검색 기능 미완료 |
| N14 | B+C | case projection이 실제 Nemotron context·tool trace에 전달 | 저장만 됐으면 RAG 동작 주장 금지 |
| N15 | C | log/Issue 동시 입력·bot comment 반복·API body hash409 | 두 입력 무인 자동화 완료 주장 금지 |

NVIDIA 사용 심도는 실제 문제 판단·도구 선택·추적·보호에서 보여준다. 이력 기능을 넣었다고 NVIDIA embedding이나 NeMo Retriever까지 사용한 것이 아니다. 실제로 쓰지 않은 제품은 계획으로 표시한다. API endpoint·model ID는 이 문서의 후보명보다 팀이 시험한 manifest를 우선한다.

GitHub의 API 버전·GitHub.com/GHES 지원 범위는 설치 형태에 맞춰 고정한다. 공식 예제에 보이는 최신 버전 문자열을 모든 환경에 적용 가능하다고 가정하지 않는다. 댓글 수신자가 GitHub 알림을 설정했는지, 메일/메신저를 별도 필수 채널로 볼지도 킥오프에서 정한다.
