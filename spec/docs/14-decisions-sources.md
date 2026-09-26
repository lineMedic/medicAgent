# 14. 변경 결정·출처·미확인 항목

> v4 결정 기록. D01~D27은 v2/v3의 역사이며, 현재 변경은 D28 이후와 현행 규범 문서를 따른다. W01~W12의 서술은 v3에서 계승한 참고로, 이번에 해당 제품을 실측했다는 뜻이 아니다. 이번 새 공식 확인은 W13~W20이다. 외부 자료는 기술의 일반적인 동작 확인에만 사용했다. 원안에 기록된 팀 환경, 대회 인정, 코드 구현 상태를 외부 자료로 대신 확정하지 않는다.

## 1. 출처의 종류

| 표기 | 의미 | 사용 범위 |
|---|---|---|
| A. 원안 | 사용자 제공 `붙여넣은 텍스트(1).txt`, 「LineMedic 마스터 플랜 — Korea Agentic AI Hackathon 온라인 예선」 | 제품명·MES 도메인·원래 설계·일정 기록 |
| B. v2 결정 | v2 패키지에서 정한 개발 계약 | 범위 축소, API/SQL/권한/검증/작업 명세 |
| B3. v3 결정 | v2 패키지 검토 후 이번 수정에서 정한 결정 | core/hardening 분리, 절대 일정, GitHub 계정 구조, S3-C 단순화, 호스트 확정 |
| C. 공식 문서 확인 | 계승 W01~W12, 이번 확인 W13~W20 | 기술의 문서화된 일반 기능·제약 |
| B4. v4 결정 | 사용자 신규 Issue/알림/기억 요구와 v3 원본 18개 검토 | [검토 보고서](../V3-REVIEW-AND-V4-CHANGES.md)와 15~18 |
| D. 미확인 | 실행·권한·답변·실측이 필요한 항목 | 구현했다고 쓰지 않고 확인 작업으로 남김 |

A의 문서 상태는 **구현 전 설계안**이다. v2도 마찬가지다. 패키지에 들어 있는 JSON·YAML·SQL·명령은 개발 계약·예시이며 완성된 애플리케이션이 아니다.

원안 파일의 SHA-256: `ebb3c15226ca9a1335fce4ff34ad2fad159a694574d0774847f245fed8d2f02f`  
원안 작성 표기: `2026-09-26 23시`. v2는 원안을 대체할 수정 설계 문서이며, 원본 파일은 수정하지 않았다.

원본 전문을 패키지에 복제하지 않아 오래된 계약을 두 개의 현행 기준으로 만들지 않는다. 인용 위치는 원안의 절 번호와 제공된 줄 번호를 함께 적는다. 편집기로 보는 줄 번호는 추출 방식에 따라 다를 수 있으므로 절·내용도 대조한다.

## 2. 원안에서 유지한 것

| 원안 위치 | 유지 내용 | v2 문서 |
|---|---|---|
| §1, L53~59 | 코드 수정·설비 점검 분기, 제안과 외부 실행 분리, 검증기만 복구 판정 | 00·01·02·04 |
| §3, L133~145 | 가상 공장, 사람 승인, 실제 데이터와 측정하지 않은 주장을 쓰지 않음 | 01·07·13 |
| §7.1, L326~340 | MES 불량 집계, `inspector_id` 누락, 합성 fixture | 08·09 |
| §7.6, L469~477 | 로그를 비신뢰 입력으로 처리, repo 사본, 증거 인용, 예산 | 05 |
| §17, L1193~1213 | 독립 verifier → 사람 제안 통합 → agent 연결 | 10 |
| §21, L1424 | Kubernetes·다른 주문 도메인으로 되돌아가지 않음 | 00·02 |

## 3. 변경 결정 기록

| ADR | 원안의 문제 또는 기존 선택 | v2 결정 | 이유·대가 |
|---|---|---|---|
| D01 | S2·S1b가 P1 | S2-lite·S1b를 제품 P0로 | 차별점을 먼저 입증; full 정비 흐름은 삭제 |
| D02 | work order·현장 안내·정비 후 복구 | **로컬 정비 요청 초안**만 | 실제 정비 권한·시스템 불필요; 복구 실적 아님 |
| D03 | 자동 머지 감시·변경 창 | 사람의 명시적 exact SHA 배포 명령 | 통합량 감소; 자동 배포 스케줄러 아님 |
| D04 | base에서 검사, 최신 main 배포 | base→candidate→PR head→merge→image→verification | 검사·배포 대상 차이를 추적; 충돌 시 중단 |
| D05 | reset이 main·DB·로그를 되돌림 | run별 보호 baseline branch, 과거 run·PR 보존 | 반복 평가와 증거 유지; branch 보호 사전 확인 필요 |
| D06 | `app/`, `tests/` 광범위 변경 | `app/defects.py` + 새 repro 파일 1개 | 보호 회귀·설정 손상 방지; 범용 버그 수정은 제외 |
| D07 | 재현 gate가 설비 원인 오판을 막는다고 주장 | 재현·회귀·업무·조치 적절성을 구분 | 테스트 공동작성의 한계 인정; 사람 검토 유지 |
| D08 | evidence ID면 근거가 맞음 | 존재·사건 소속·버전·시간의 출처 연결만 | 의미 타당성은 별도 문제 |
| D09 | runner·MES가 신뢰 영역에 표시 | 생성 코드와 실행 컨테이너는 비신뢰 | agent 격리만으로 배포 코드 위험이 해결되지 않음 |
| D10 | `/tools`·`/ops` 경로/출발지 위주 | 서버 측 principal·scope, 분리된 관리 접근 | 클라이언트 actor·경로 명칭을 권한 근거로 쓰지 않음 |
| D11 | 단일 worker이면 동시성 제거 | 짧은 DB transaction·CAS·intent·reconcile | 프로세스 내부 task·재시작·외부 API 불명확성 처리 |
| D12 | 수동 resolve·자동 재조사 | verifier만 RESOLVED, 실패 이관·자동 재조사 없음 | 상태 충돌과 무한 반복 제거 |
| D13 | 다수 runtime·critic·confidence 정책 | runtime 하나, critic P1, confidence 승인/강등 삭제 | 복잡도·자의적 수치 의존 감소 |
| D14 | 연결 실패로 정책 차단 확정 | 대조 연결 + 거절 기록 + 실패 주체 분리 | 이름 해석·서버 부재와 정책 거절 구분 |
| D15 | NAT 폴백이면 NeMo 조건 해결 | runtime 선택과 R1/R2 인정을 분리 | 주최 측 답변 없이는 충족 주장 금지 |
| D16 | 사람 통합 + agent PR이면 전체 성공 | manual integration과 actual agent E2E 별도 CP | 사람 산출물이 모델 성능을 대신하지 못함 |
| D17 | 4테이블 + 직접 JSONL append | 7테이블, DB audit_events가 원본·JSONL export | 원자적 상태/감사 기록; tamperproof 시스템은 아님 |

### v3 변경 결정

| ADR | v2의 문제 또는 기존 선택 | v3 결정 | 이유·대가 |
|---|---|---|---|
| D18 | P0 최저선에 강건성 장치가 모두 포함돼 48시간 공수 초과 | P0를 core / hardening(H01~H07)으로 분리 | 완료 가능한 경로 확보. hardening 미완료는 `설계됨/미구현`으로 공개 |
| D19 | T+ 상대 일정, 월요일 근무 전제 없음 | KST 절대 일정. 일·월 전일 작업(월요일 휴가) | 체크포인트가 실제 시각에 작동 |
| D20 | 샌드박스 미통합도 공개로 허용 | 샌드박스 안 에이전트 실행을 core로, hardening보다 먼저 | 심사 1번 항목과 교육 미션 대응 |
| D21 | 브로커 credential 미정, 실행마다 보호 설정 | GitHub App/봇 계정이 PR 작성, 다른 팀원이 리뷰, `baseline/*` 패턴 보호, squash 단일 머지 | PR 작성자는 자기 PR을 승인할 수 없음(W11). 패턴 보호 지원(W12). run_id에 `/` 금지 |
| D22 | S3-C 별도 대조 샌드박스 | 호스트 대조 + 같은 샌드박스 허용/금지 + 거절 로그 | 비용 절감. 거절 로그 없는 실패는 여전히 UNATTRIBUTED |
| D23 | 데모 호스트 미정 | 킥오프에서 1대 확정, 모든 평가를 같은 호스트에서 | OpenShell README는 macOS·WSL 2·Linux 호스트를 적고 있음(W10). v2 검토 과정에서 "Linux 필수"라고 한 판단은 이 근거로 정정 |
| D24 | 멱등 키 본문 해시·409가 P0 | core는 DB unique 논리 키, 409는 H02 | 단일 supervisor 데모에서 중복 외부 작업 방지는 unique 키로 충족 |
| D25 | 결과 불명 자동 reconcile이 P0 | core는 사람이 실행하는 `make reconcile`, 자동 재조회는 H04 | 재실행 금지 원칙은 core로 유지 |
| D26 | observer heartbeat·cursor가 P0 | core는 로그 스트림 연속 읽기 + container·image 불변, heartbeat는 H03 | 60초 관찰 원칙 유지 |
| D27 | 신청서 초안 192자·278자, 내부 코드명 포함 | 약 300자·450자로 재작성, 코드명 제거 | 문항 기준에 맞춤. 실제 구현과 다르면 문장 삭제 |

### 원안의 특정 주장을 어떻게 바꿨나 (v2)

- §12.3 L1004의 ‘설비면 재현 테스트가 통과하므로 차단’은 삭제했다. 모델이 테스트와 패치를 함께 만들 수 있어 원인 판별 보장이 성립하지 않는다.
- §7.8 L567의 ‘main 최신 커밋’은 승인된 최종 SHA로 대체했다.
- §9.2 L814의 사람 `RESOLVED` 전이는 제거했다.
- §10.4 L883~897의 프로브는 살아 있는 모의 sink 대조와 거절 주체 확인을 추가했다.
- §19.3 L1337의 ‘무개입 실행’은 사람 검토·승인 전후 구간 자동화로 수정했다.
- §20.3 L1392의 sandbox 탈출 피해가 작다는 설명은 삭제했다. host 침해의 피해 범위를 해당 설계만으로 보장하지 않는다.

## 4. 공식 자료와 적용 범위

아래 W01~W12는 v3가 남긴 기술 출처를 계승했고, W13~W20은 v4 설계 확장을 위해 이번에 공식 내용을 확인했다. 어느 쪽도 팀의 실제 설치·호출·정책 적용을 실행한 결과가 아니다. `latest` 문서와 패키지 버전은 달라질 수 있어 구현 시 선택한 버전으로 다시 확인한다.

<a id="w01"></a>
### W01. NVIDIA NeMo Agent Toolkit

[공식 개요](https://docs.nvidia.com/nemo/agent-toolkit/latest/index.html)

도구 연결, 프레임워크 간 사용, 프로파일링·관찰·평가 기능을 문서화한다. v2의 runtime adapter와 실제 trace 활용 방향을 뒷받침한다. 정확한 함수·설정 스키마나 해커톤 인정 여부의 근거는 아니다.

<a id="w02"></a>
### W02. NVIDIA OpenShell sandbox policies

[공식 정책 개요](https://docs.nvidia.com/openshell/latest/how-it-works/policies/overview)

파일·프로세스·네트워크 정책과 적용 구조를 설명한다. v2는 실제 effective policy를 기록하고 허용·금지 대조 시험을 요구한다. 이 패키지에는 검증하지 않은 OpenShell 정책 YAML이나 실행 명령을 제공하지 않는다.

<a id="w03"></a>
### W03. GitHub Pull Request REST API

[공식 Pull Requests 문서](https://docs.github.com/en/rest/pulls/pulls#get-a-pull-request)

PR의 merge 관련 필드는 병합 전후와 병합 방식에 따라 의미가 달라진다. v2는 `merged=true`, 최종 merge SHA, 실제 source tree를 확인한 뒤 고정 버전을 검사·배포한다. 병합 전 merge commit 정보를 최종 승인 코드로 사용하지 않는다.

<a id="w04"></a>
### W04. GitHub protected branches

[공식 보호 브랜치 문서](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches)

필수 review, 새로운 변경과 승인 관계, 보호 설정을 설명한다. 팀 저장소에서의 사용 가능 여부와 bypass 권한은 별도로 시험한다. v2의 run별 baseline branch가 실제 보호됐다고 이 문서만으로 주장하지 않는다.

<a id="w05"></a>
### W05. pytest exit codes

[공식 종료 코드](https://docs.pytest.org/en/stable/reference/exit-codes.html)

통과·테스트 실패·실행 중단·내부 오류·사용 오류·미수집 등을 구분한다. v2에서 비정상 종료 전체를 ‘버그 재현’으로 취급하지 않는 근거다. runner의 pytest 버전에 따라 실제 코드와 리포트를 확인한다.

<a id="w06"></a>
### W06. SQLite transactions

[공식 transaction 문서](https://sqlite.org/lang_transaction.html)

SQLite의 writer·transaction과 `BEGIN IMMEDIATE` 동작을 설명한다. v2는 짧은 transaction에 상태 전이·실행 intent·감사 이벤트를 함께 기록하고 외부 호출은 밖에서 수행한다. 애플리케이션 전체의 exactly-once를 보장한다는 뜻은 아니다.

<a id="w07"></a>
### W07. Docker Engine security

[공식 보안 개요](https://docs.docker.com/engine/security/)

Docker daemon 권한, host 영향, 컨테이너 격리와 보안 설정을 설명한다. v2는 Docker를 제어하는 host supervisor를 신뢰 경계로 두고, agent·runner·MES에 Docker socket과 관리 credential을 노출하지 않는다. 컨테이너 탈출 불가를 주장하지 않는다.

<a id="w08"></a>
### W08. Docker run reference

[공식 run 문서](https://docs.docker.com/engine/containers/run/)

컨테이너 실행·이미지 지정·격리/자원 설정·종료 코드 의미를 설명한다. v2의 runner 실행 예시는 검증해야 할 설정안이며, host 환경에서 실제 실행한 기록이 아니다. Docker 실행 오류와 test 결과를 구분한다.

<a id="w09"></a>
### W09. NeMo Guardrails deployment forms

[공식 Library API Server / Microservice 구분](https://docs.nvidia.com/nemo/guardrails/more-deployment-options/using-microservice)

문서는 Library API Server와 NeMo Microservices 플랫폼의 Guardrails Microservice를 구분한다. OSS 라이브러리를 API로 감싼 것을 후자의 배포 실적으로 설명하지 않는다. 대회에서 어느 형태를 인정하는지는 R2의 별도 확인 사항이다.

<a id="w10"></a>
### W10. NVIDIA OpenShell README

[GitHub 저장소](https://github.com/NVIDIA/OpenShell)

README의 요구사항에 "A supported host — macOS, Windows with WSL 2, or Linux"와 "A local runtime — Docker, Podman, or host virtualization enabled for MicroVM-backed sandboxes"가 있다. 정확한 커널·버전 조건은 README가 안내하는 Support Matrix와 실제 설치로 확인한다(N09).

<a id="w11"></a>
### W11. GitHub: 필수 리뷰가 있는 PR 승인

[공식 문서](https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/reviewing-changes-in-pull-requests/approving-a-pull-request-with-required-reviews)

"Pull request authors cannot approve their own pull requests." v3가 브로커를 별도 봇 credential로 분리하는 근거다.

<a id="w12"></a>
### W12. GitHub: 브랜치 보호 규칙 관리

[공식 문서](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/managing-a-branch-protection-rule)

`fnmatch` 이름 패턴으로 규칙을 만들 수 있고, `*`는 디렉터리 구분자 `/`와 일치하지 않는다(`qa/*`는 `qa/foo/bar`와 불일치). v3의 `baseline/*` 규칙과 run_id 형식 제한의 근거다. 우회 금지 설정의 실제 효과는 N07에서 시험한다.

## 5. 이번 패키지에서 확인하지 않은 것

| 항목 | 상태와 처리 |
|---|---|
| 사용자 팀의 실제 코드·PR·실행 환경 | 제공되지 않음. 구현 성과를 평가하거나 완료로 표시하지 않음 |
| 원안에 적힌 model ID·버전·gateway API | 환경 실측 없음. N01~N08 스파이크로 확인 |
| 교육 과정 전체·비공식 한국어 사이트 | 원안에 참고 링크가 있으나 이번 작성에서 전문을 확인하지 않음. 구체적 module/API 보장으로 사용하지 않음 |
| 타 팀의 공개 저장소·상대 우위 | 별도 검증하지 않음. 경쟁팀 기능·수상 전망 주장을 삭제 |
| 해커톤 신청서 전체·주최 측 인정 | 로그인 자료·답변 없음. R1~R5로 유지 |
| OpenShell Support Matrix 세부 조건 | README 요구사항만 확인. N09에서 실제 호스트로 확인 |
| 팀원 가용 시간 | v3의 20~24시간은 가정. v4는 남은 시간과 완료 W를 확인하고 확률 수치를 사용하지 않음 |
| 실제 공장 성능·정비 수요·보안 인증 | 합성 환경만 설계. 검증 실적으로 주장하지 않음 |
| `LineMedic 설계서` 등 원안이 다시 참조하는 별도 문서 | 현재 제공된 마스터 플랜 이상을 읽었다고 가정하지 않음 |

## 6. 문서 변경 규칙

v4에서 API field·enum·조치 경계·검증 조건을 바꾸면 해당 문서와 관련 테스트·시나리오·발표 문구를 함께 수정한다. 내부 모듈 이름을 바꾸는 작업과 공개 계약 변경을 구분한다. 변경표에 `결정 / 이유 / 영향 문서 / 필요한 재시험`을 한 줄 남긴다.

기술 확인 결과로 현재 가정이 틀렸다면 가장 작은 계약 수정으로 반영한다. 잘못된 보안·검증 주장을 유지하기 위해 결과를 성공으로 바꾸지 않는다.


## 7. v4 변경 결정

| ADR | 결정 | 이유·대가 |
|---|---|---|
| D28 | log/Issue 두 입력을 work item으로 통합 | Issue-first 요구 충족, intake 모듈 추가 |
| D29 | 안정 fingerprint와 routing_scope 분리 | 반복 장애 검색과 평가 격리를 함께 지원 |
| D30 | 확정 binding 우선·유사도 후보·incomplete 정지 | 중복과 잘못된 연결을 줄이되 완전 중복 탐지 주장 안 함 |
| D31 | 한 repo outbound polling core, webhook P1 | 공개 수신 infrastructure 부담 축소, 탐지 지연과 rate-limit 고려 |
| D32 | 승인 actor·명시 scope·기존 작업 보호 | 새 Issue가 공격 실행 권한이 되는 것을 방지 |
| D33 | 시작 알림 receipt 전에 수정 금지 | 사용자 요구의 순서를 host에서 보장, provider 장애 시 작업 지연 |
| D34 | durable notification outbox·UNKNOWN | 로컬 기록/외부 접수/사람 수신 분리 |
| D35 | H01·H02 core 승격 | 두 입력의 중복 claim·키 충돌은 필수 correctness |
| D36 | case outcome을 실제 verifier/phase/origin으로 분리 | PR-only·권한 차단을 정답/오답으로 오해하지 않음 |
| D37 | SQLite lexical retrieval core | 별도 vector 인프라 없이 검색→context 경로 완성 |
| D38 | cold_start/memory_assisted snapshot 분리 | 과거 해답 재사용과 기본 능력 평가의 충돌 방지 |
| D39 | 성공 알림과 복구 판정 분리 | 전송 장애가 확인된 업무 결과를 뒤집지 않음 |
| D40 | wire schema linemedic.v4, 무중단 호환 가정 제거 | work identity 등 필수 계약 변경·migration 필요 |
| D41 | 확률 대신 남은 시간·증거 CP | 기능 증가를 48시간 내 공짜 확장으로 취급하지 않음 |

## 8. v4에서 새로 확인한 공식 자료

<a id="w13"></a>
### W13. GitHub REST Issues

[공식 문서](https://docs.github.com/en/rest/issues/issues)

Issue 목록·생성·조회 계약과 pagination을 확인했다. Issues 응답에 PR도 포함될 수 있으므로 `pull_request`를 제외한다. scope 완전성·fingerprint matching·운영자 승인 정책은 **LineMedic의 추가 설계**다. GitHub가 자연어 중복 판정이나 외부 exactly-once를 제공한다고 가정하지 않는다.

<a id="w14"></a>
### W14. GitHub Issue comments

[공식 문서](https://docs.github.com/en/rest/issues/comments)

댓글 생성 endpoint는 알림을 트리거하며 과도한 생성에 rate limit이 걸릴 수 있다. v4는 댓글 ID를 provider receipt로 저장한다. ‘사람이 받거나 읽음’은 이 receipt의 의미가 아니다.

<a id="w15"></a>
### W15. GitHub REST API best practices

[공식 문서](https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api)

GitHub는 polling보다 webhook을 권고한다. polling이 필요한 경우 효율적 주기·조건부 요청·rate limit을 고려한다. v4의 60초·overlap·페이지 cap·단일 writer는 예선용 선택이지 공식 추천 성능 수치가 아니다.

<a id="w16"></a>
### W16. Webhook signature validation

[공식 문서](https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries)

선택 webhook 어댑터는 secret 기반 `X-Hub-Signature-256` 검증을 적용한다. payload signature는 GitHub 전송 검증이며 그 안의 Issue 지시가 안전하거나 허가됐다는 뜻이 아니다.

<a id="w17"></a>
### W17. Webhook best practices

[공식 문서](https://docs.github.com/en/webhooks/using-webhooks/best-practices-for-using-webhooks)

최소 event 구독, 빠른 응답과 비동기 처리, event/action 확인, delivery 식별·재전송을 고려한다. redelivery는 동일 delivery ID를 사용할 수 있다. core는 아직 webhook 구현을 요구하지 않는다.

<a id="w18"></a>
### W18. PR과 Issue 연결

[공식 문서](https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/linking-a-pull-request-to-an-issue)

closing keyword에 의한 Issue 종료는 기본 브랜치 등 GitHub 규칙을 따른다. v4는 업무 검증 전 자동 종료와 혼동하지 않도록 중립적인 parent Issue 참조를 사용하며 core의 자동 close는 제공하지 않는다.

<a id="w19"></a>
### W19. GitHub 알림 설정

[공식 문서](https://docs.github.com/en/subscriptions-and-notifications/get-started/configuring-notifications)

알림 구독·전달 설정은 사용자별이다. GitHub 댓글 접수와 특정 수신자의 이메일 도착·열람을 동일하게 표시하지 않는다.

<a id="w20"></a>
### W20. SQLite FTS5

[공식 문서](https://www.sqlite.org/fts5.html)

FTS5는 전문 검색·BM25 순위·snippet 기능 등을 제공한다. v4는 SQL 필터·snapshot·note identity를 결합해 lexical 조회에 사용한다. 한국어 의미 검색·embedding·RAG 품질 향상을 자동 보장하지 않는다. 설치 환경의 FTS5 지원과 tokenizer는 실제 시험한다.

확인 기준일: 이 문서 작성일(2026-09-27). 기술 문서 확인은 팀 환경 실측·API 권한·발송·대회 인정의 증거가 아니다.
