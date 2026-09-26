# v3 검토 결과와 v4 변경 보고서

> **검토 대상은 이번에 업로드한 `LineMedic_Development_Pack_v3.zip`의 Markdown 18개 전부다.** 이전 1,512행 원안이나 v2 요약으로 v3를 대신 평가하지 않았다. 실제 제품 코드·GitHub 작업·운영 실행 결과는 ZIP에 없어 구현 수준을 평가하지 않는다.

## 1. 결론

v3의 방향은 타당하다. core/hardening을 나누고, 실제 sandbox 실행·봇 PR과 다른 사람 리뷰·보호 baseline·같은 호스트 평가를 명시한 것은 개발 판단에 유용하다. 실제 배포 검증과 생성 코드 격리도 유지됐다.

다만 사용자가 이번에 요구한 **Issue-first 자동화, 작업 시작 알림, 실행 불가 외부 전달, 경험 이력 검색**은 아직 하나의 실행 계약으로 연결되지 않았다. ‘Issue’, ‘지식’, ‘이관’이라는 단어가 있다는 것과 해당 기능이 실제 단계·API·상태·저장소·시험으로 정의됐다는 것은 다르다.

v4는 기존 방향을 바꾸지 않고 입구(Issue)와 출구(알림·사례)를 붙인다. 이를 모든 장애 자동 수정이나 범용 agent 조직으로 확장하지 않는다.

## 2. 요청별 포함 여부

| 이번 요청 | v3 판정 | v3에서 확인한 내용 | v4 변경 |
|---|---|---|---|
| 로그 분석 → repo에서 Issue 검색 → 없으면 생성 | **미포함/후순위** | master §3에서 GitHub Issue 연동은 P1 | 15의 lookup·matching·create intent·binding |
| 같은 Issue가 있으면 해당 작업 수행 | **부분 개념만** | broker는 동일 PR 재사용, Issue-driven work 없음 | 기존 Issue+활성 work+사람 작업 충돌 구분 |
| 새 Issue 감시 | **미포함** | API·dispatcher가 incident 입력 중심 | bounded polling·checkpoint·초기 backlog 제외·author opt-in |
| 작업 시작 전 알림 | **미포함** | broker core 댓글 업데이트 제외, 외부 시작 게이트 없음 | receipt 확인 전 writable workspace/attempt 금지 |
| 불가능한 작업 이유 정리 | **부분 포함** | escalate summary/reason/open_questions 있음 | phase·시도·부작용·자료·재개조건을 blocker 계약으로 보강 |
| mail 또는 기타 notification | **미포함/후순위** | 로컬 초안/상태·dashboard, 발송 adapter/outbox 없음 | GitHub 댓글 기본 또는 선택 SMTP 1개, 실제 receipt·실패 기록 |
| error log·개선·결과 저장 | **부분 포함** | evidence/proposal/execution/verification/audit 존재 | case note로 원본을 연결하고 검증 수준별 결과 정리 |
| 정답/오답노트 검색·RAG | **미포함/후순위** | knowledge API는 정적 매뉴얼·런북, RAG는 축소 대상 | exact+SQLite FTS, note 인용, cold/memory 평가 분리 |

‘없음’은 전체 18개 문서를 확인한 **명세 포함 여부**다. 별도 저장소에서 이미 구현했는지 여부는 확인하지 않았다.

## 3. 좋은 보강과 유지한 이유

| v3 보강 | 평가 | v4 처리 |
|---|---|---|
| core/hardening 분리 | 필수 결과와 추가 완성도를 구분 | 유지하되 두 입력에 필수인 claim·body hash409를 core로 |
| 실제 sandbox 에이전트 core | 도구 설치와 제품 통합을 구분 | 유지, local/보안 프로브와 실제 경로 분리 |
| GitHub App/봇과 다른 리뷰어 | 작성/승인 주체가 명확 | 유지, Issue 권한을 별도로 추가 |
| `baseline/*`·squash | 반복 평가와 exact code 연결에 유리 | 유지, Issue는 branch 격리 밖이므로 scope 추가 |
| 동일 호스트·S3 대조 명확화 | 환경 변화·차단 원인 혼동 감소 | 유지, 실제 지원 환경은 미검증으로 표시 |
| 자동 reconcile 대신 사람이 조정 | 48시간의 복잡도 절약 | 유지, UNKNOWN의 맹목 재실행 금지는 필수 |
| 정비 초안·S1b·독립 검증 | 공장 제품의 차별점을 지킴 | 유지, 인계/수정안/업무 복구 상태 별도 알림 |

## 4. 추가로 고친 논리

**1. 입력이 두 개이면 ‘단일 supervisor라 경합 시험은 나중’이 충분하지 않다.** 로그와 poll이 같은 Issue에 동시에 도착할 수 있다. work unique·CAS·same-key/different-body409를 core로 올렸다.

**2. run을 포함한 fingerprint만으로는 장기 경험을 찾기 어렵다.** 안정된 problem fingerprint와 routing_scope/run을 분리했다. 평가의 교차 run 오염은 scope와 memory snapshot으로 통제한다.

**3. 결과 기록과 사용자 알림은 다르다.** outbox, receipt, UNKNOWN, 시작 gate, 실패 원인 템플릿을 추가했다. 모든 상태를 notification 성공 여부와 동일시하지 않는다.

**4. 원본 audit는 정답·오답노트가 아니다.** 실제 verifier 결과와 출처로 SUCCESS/FAILURE/UNVERIFIED/BLOCKED/INCONCLUSIVE/HANDOFF를 나눴다. 권한 부족을 틀린 해결책으로 저장하지 않는다.

**5. 이전 정답 차단 규칙과 이력 재사용 요구가 충돌할 수 있다.** holdout은 항상 제외하고, cold_start에서는 사례 차단, memory_assisted에서만 사전 고정한 과거 기록을 제공한다. 자기 결과를 같은 실행의 정답으로 되먹이지 않는다.

**6. 기존 완료 확률과 일정은 새 범위를 설명하지 못한다.** v3의 확률은 실측 근거가 없고 실제 가용 시간도 비어 있었다. v4는 W 상태·남은 시간·실행 CP로 바꿨다. 기존 48시간 일정이 다시 시작되는 것처럼 쓰지 않는다.

**7. 문서 버전과 wire version을 분명하게 했다.** v3는 `linemedic.v2`를 유지했고 인계 템플릿에도 v2가 남아 있었다. v4는 의도적인 `linemedic.v4` 변경과 client/DB 전환 절차를 명시했다. 자동 호환된다고 가정하지 않는다.

## 5. 변경 범위

기존 18개 MD를 직접 갱신했고, 다음 규범을 새로 추가했다: 15 Issue intake, 16 notifications, 17 case memory, 18 migration/validation. API03·상태04·요구01·시험09·작업10·runbook11·발표13에 같은 내용을 연결했다. 단순한 추가 메모만 붙여 기존 상충 규칙을 그대로 유지하지 않도록 점검했다.

새 알림 채널을 다 만들지 않는다. GitHub 댓글 하나가 core 기본이다. 이메일이 실제 필수면 SMTP를 선택해 같은 receipt/실패 계약을 구현한다. 장기 이력 검색은 lexical부터 시작하며 임베딩·벡터 DB는 선택 확장이다.

## 6. 원문 위치와 발췌

아래 행 번호는 **ZIP 안 UTF-8 원본 MD의 물리적 행 번호**다. ChatGPT Files citation ID의 행 번호가 아니며, 해당 번호를 filecite 문법으로 변환하지 않았다. 결론은 일부 문구만 검색한 결과가 아니라 전체 파일 읽기와 대조에 기반한다.

### `00-MASTER-PLAN.md` · 물리적 51행

> 설비 정비 완료 후 업무 검증, CMMS·메신저·GitHub Issue 연동, 두 번째 코드 버그 유형, 자동 머지 감시·변경 창 스케줄링, 비평가, 매뉴얼 RAG, 규칙 기반 비교, 온프레미스 NIM. 미구현 항목을 신청서의 사용 기술로 넣지 않는다.

### `docs/03-api-contracts.md` · 물리적 50행

> | `get_knowledge` | GET `/tools/incidents/{incident_id}/knowledge` | `q` 선택 | 허용된 정적 매뉴얼·런북 절. 임의 URL fetch 금지 |

### `docs/03-api-contracts.md` · 물리적 158행

>     "type": "escalate",

### `docs/06-broker-runner.md` · 물리적 103행

> - P0는 댓글 업데이트 기능을 추가하지 않는다. 이미 있는 동일 PR의 reference를 반환한다.

### `docs/06-broker-runner.md` · 물리적 116행

> 외부 CMMS·GitHub Issue를 만들지 않는다. 서버 catalog의 equipment와 manual reference를 확인한 뒤 execution.result에 초안을 저장하고 `WORK_ORDER_DRAFTED`로 전이한다. 초안에는 다음만 포함한다.

### `docs/10-delivery-plan.md` · 물리적 118행

> **먼저 줄일 것:** 화면 확장, 자동 알림, 추가 runtime, 비평가, RAG, 자동 배포 감시, 두 번째 버그 유형, hardening H01~H07.

### `docs/04-data-state.md` · 물리적 225행

> 키는 `(run_id, service, normalized_error_or_metric_signature)`를 기반으로 계산한다. P0 초기 기준은 같은 fingerprint 60초 내 3회다. 직접 검사에서 실패한 업무 계약도 `business_contract_violation` 사건을 생성할 수 있다.

### `docs/03-api-contracts.md` · 물리적 11행

> - 변경 요청은 `Idempotency-Key` 필수. 키 범위는 `(principal, method, path, run_id)`다. `[core]` 같은 키는 DB unique 제약으로 기존 결과를 반환하고 새 외부 작업을 만들지 않는다. `[hardening H02]` 정규화한 요청의 SHA-256을 함께 저장해 같은 키·다른 내용이면 409를 반환한다. H02 미구현 시 "같은 키의 다른 본문은 첫 요청 결과로 처리됨"을 README에 적는다.

### `00-MASTER-PLAN.md` · 물리적 107행

> | core 전체 | 샌드박스 안 에이전트 S1 전체 경로 + S2-lite + S1b + S3 분리 증거 + 요건 확인 + 재현 문서 | **65~75%** |

### `templates/implementation-handoff.md` · 물리적 38행

> LineMedic v2의 작업 [W__]만 구현하라.


## 7. 출처·재현

기술 확장에는 GitHub 공식 REST/Webhook/알림 문서와 SQLite FTS5 문서를 추가 확인했다. 이는 v3에 이미 구현된 기능의 증거가 아니라 **v4에서 선택한 설계의 참고 근거**다. 원문 URL과 적용 범위는 [14](docs/14-decisions-sources.md)의 W13~W20에 있다.

원본 ZIP SHA-256: `795f863086209fe2f0d00dd50a2adf5cb73bd750f26e8e0edd5efe8333e31543`

| v3 원본 파일 | SHA-256 |
|---|---|
| `00-MASTER-PLAN.md` | `58e0b38819a9062edb9880f4cadbccf02fcc1ba85e86a0f04dc3c57a3256ee8c` |
| `README.md` | `1e01c8525fd9f374133750e5c544f1f3baa00b99e70db839d96112f1833516f9` |
| `docs/01-requirements.md` | `68ab9081d4b46fe30a2ed4d03a5f195d8cde84719528c0c7a512c64e9656645d` |
| `docs/02-architecture.md` | `27149bc11a5dcddb07d6608eb98bfd184e8a2527b2f4504092c3b797fa8c5813` |
| `docs/03-api-contracts.md` | `5588737aaf60ed0a29b5b63484be2045352d2e063b4e23ba0a45f627b7ef9f96` |
| `docs/04-data-state.md` | `d5919921814cadddefefddfbec4f08a9c7ddd7849147b1e672eb8ef5525b0d53` |
| `docs/05-agent-spec.md` | `4bebe5ca0db6060a06ea7186070cd92268beca020a0caae612511ef2bfeb2766` |
| `docs/06-broker-runner.md` | `3cae9a625123704dbe4f2ec108c8423f7668e67f42744e306e9e3828c1f9faad` |
| `docs/07-security.md` | `3256e7c7edf3f43c7703e08939beb77ae1694f55bf6e3c91e19ffb1a197c49a4` |
| `docs/08-release-verification.md` | `f601a1a572874c5b849f839928a366a2b4f0c57d904bcd54c2e56474b6a2ab57` |
| `docs/09-scenarios-evaluation.md` | `e6d2be4b86e64b76e3dee60a0f1b1e12a667a1ae9579ac1d54769731c32b4318` |
| `docs/10-delivery-plan.md` | `1ff55ffb465d48065bea84be3f001afe8dc0b7afcbf8f11fe0ec93eb14160271` |
| `docs/11-runbook.md` | `0d7054dbd5dc76f5b3c6a25773e70dc5852a238ad58d91a03377efcbc0d96272` |
| `docs/12-nvidia-requirements.md` | `a48d74d54b33fd7b0579efd0b0859b56053a416c36889bf411d4a869fd4aa984` |
| `docs/13-demo-submission.md` | `b5fdc64d3da6166245bce9955ab02fc8d68e54b18aacd3989d15265402e8de26` |
| `docs/14-decisions-sources.md` | `3294d9c3beed951132c5f84f701e1db94b33e1499d64498624978514063866cd` |
| `templates/implementation-handoff.md` | `eb3b735f3fa2ace4699b1314a0ed02600c5c885d9f66a83d3aa6c4e5b4d19751` |
| `templates/run-record.md` | `b9e7d4454dfb34bf3324a9d10035943a44be84818e6195f94e1c223f5a881a95` |

원본 ZIP과 추출본은 변경하지 않았다. 패키지 생성 시 수행한 문서 검사는 [검증 결과](PACKAGE-VALIDATION.md)에 기록한다. 실제 모델·GitHub·알림·배포는 이번 문서 작성에서 실행하지 않았다.
