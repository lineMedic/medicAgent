# 17. 오류·개선·결과 이력과 정답·오답노트 검색

> **v4 신규 규범 문서.** 감사 로그 저장과 검색 가능한 운영 지식은 다르다. 이 문서는 과거 시도·검증 결과를 구조화해 현재 조사에 제공하는 최소 사례 기억을 정의한다. 모델 자체의 학습·fine-tuning을 수행하지 않는다.

## 1. 무엇을 저장하는가

case note에는 증상, 정제된 오류 signature, 원인 가설, 실제 시도와 patch/PR 참조, 검사 결과, 업무 검증 결과, 실패 이유, 적용 조건, 남은 질문을 남긴다. 파일 전체 복사 대신 evidence ID·content hash·code/image/contract identity로 원본을 연결한다.

**Issue 댓글, 모델의 ‘완료’라는 문장, PR merge 상태만으로 성공/실패를 확정하지 않는다.** phase와 근거 수준을 함께 보존한다.

| outcome | 노트 성격 | 생성 조건 |
|---|---|---|
| `VERIFIED_SUCCESS` | 검증된 정답 사례 | verifier PASS + 정확한 image/contract/관찰 범위 연결 |
| `VERIFIED_FAILURE` | 관찰된 오답 사례 | 실제 업무 검사 FAIL. 실패 assertion·조건·대상 버전 명시 |
| `UNVERIFIED` | 아직 검증 안 된 시도 | PR·test는 준비됐으나 업무 검증 없음 |
| `BLOCKED` | 실행 불가/거절 기록 | 권한·지원 범위·자료 부족·정책·source 충돌·예산 부족 등 |
| `INCONCLUSIVE` | 결론 미확인 | timeout·수집 공백·대상 불명으로 결과 판단 불가 |
| `HANDOFF` | 설비 점검 인계 사례 | 초안이 생성됐음, 실제 정비·복구 확인은 아님 |

`BLOCKED`의 policy-rejected patch는 ‘허용되지 않은 시도’이지 ‘업무 결과가 틀린 코드’와 다르다. runner의 회귀 FAIL도 `phase=validation`, reason과 관측으로 남기고 운영 적용의 VERIFIED_FAILURE와 섞지 않는다. SMTP 실패도 그 패치의 오답 근거가 아니다.

## 2. 사례 생성 시점과 신뢰 주체

host의 case builder가 다음 event를 읽는다: proposal 검사 결과, PR_READY, HANDOFF_DRAFTED, WORK_BLOCKED, verification 최종 결과. 동일 `source_event_key`는 한 번만 처리한다. agent가 직접 outcome을 써 넣는 endpoint는 제공하지 않는다.

노트의 사실 필드는 DB 원본에서 결정한다. 요약에 LLM을 선택적으로 쓰더라도 새로운 사실·성공 판정을 만들 수 없으며 모든 주장은 source ID와 연결한다. core는 고정 템플릿으로 충분하다.

내용은 revision을 추가해 보존한다. 나중에 검증 결과가 나오면 같은 series의 후속 노트를 만들고 `supersedes_id`로 연결한다. 잘못된 자료를 확인하면 publish 상태를 RETRACTED로 바꾸고 이유를 남긴다. 원문과 이전 판정은 삭제하지 않는다. live snapshot을 만들 때는 series별 최신 PUBLISHED revision을 선택한다. 이미 고정한 snapshot에서는 그때 지정한 정확한 revision/hash를 사용하며, 미래 revision으로 교체하지 않는다. 검색 인덱스는 이를 지원하도록 게시된 과거 revision도 보존한다. RETRACTED는 snapshot에 있어도 제외하고, 자료 철회로 입력 집합이 달라졌음을 결과에 기록한다.

`created_at`은 기록 시각, `observed_at`은 결과 관찰 시각이다. 정답 사례라도 특정 source·contract·fixture에서만 검증됐음을 드러낸다. 모든 과거 성공이 동일한 환경에 적용되는 영구 정답은 아니다.

## 3. 최소 구조 예시

```json
{
  "schema_version": "linemedic.case.v4",
  "note_id": "CASE-001-R2",
  "series_id": "CASE-001",
  "revision": 2,
  "supersedes_id": "CASE-001-R1",
  "repository_id": 100001,
  "service_id": "mes-api",
  "problem_fingerprint": "example-fingerprint",
  "source_run_id": "run-001",
  "source_incident_id": "INC-001",
  "work_id": "WORK-001",
  "issue_number": 42,
  "source_event_key": "verification:VER-001:final",
  "outcome": "VERIFIED_SUCCESS",
  "phase": "verification",
  "origin": "agent_release",
  "symptom": "검사자 필드 누락 입력에서 집계 예외",
  "hypothesis": "집계에서 누락 필드를 필수로 가정",
  "attempted_change": "누락된 검사자를 별도 분류하도록 변경",
  "evidence_ids": ["EV-001", "EV-002"],
  "artifact_refs": {"proposal_id": "PROP-001", "verification_id": "VER-001", "pr_number": 51},
  "applicability": {"contract_id": "defect-summary-v1", "source_identity_required": true},
  "limitations": ["합성 입력과 고정된 검사 범위에서만 확인"],
  "observed_at": "2026-09-27T14:00:00Z",
  "publish_status": "PUBLISHED"
}
```

이것은 구조 예시이지 실제 성공 기록이 아니다. 실제 저장은 검증기의 target SHA/image/contract hash와 모든 필수 근거가 있어야 위 outcome을 허용한다. `origin`은 agent_release, human_injected_negative, manual_integration, operator_note를 구분한다. 테스트용 seed는 `seed=true`로 표시하고 실제 운영 실적에서 제외한다.

## 4. 최소 검색 경로

```text
오류/Issue의 현재 서비스·signature·증상
  → repo/서비스 접근 범위와 memory snapshot 확인
  → exact problem_fingerprint 조회
  → 관련 keyword 조회 (SQLite FTS5)
  → outcome·source·contract·시각 조건 확인
  → 상위 5개, 관련 실패/차단 사례도 포함
  → note ID·근거·적용 조건을 agent에 전달
  → agent가 현재 코드·상태를 다시 조사하고 검증
```

exact와 키워드는 같은 DB에서 수행한다. FTS5는 전문 검색과 BM25 순위를 제공한다. [W20](14-decisions-sources.md#w20) 한국어 형태소·동의어를 자동 이해한다고 주장하지 않는다. 오류 코드, 함수명, 예외 필드, 서비스 ID 같은 구조화 토큰을 함께 색인한다. tokenizer와 query normalization 버전을 manifest에 저장한다.

FTS5가 설치 환경에 없으면 기능을 검증한 단순 keyword matcher로 명시적 fallback하거나 설치를 수정한다. fallback을 FTS5/embedding이라고 표시하지 않는다. FTS 쿼리는 로그 원문을 그대로 연산자로 해석하지 않고, bounded token 추출·escape·parameter binding을 한다.

ACL·repo·서비스·게시 상태·snapshot membership 필터는 **상위 k 선택 전** 적용한다. 관련 실패가 있을 때 최소 한 건을 함께 제공하되, 무관한 실패를 억지로 끼우지 않는다. outcome별 검색으로 뽑은 후보를 source 적합성·exact 여부·BM25로 합치며 top_k는 합계 최대 5다. 성공만 잘 보이게 하는 순위화를 하지 않는다.

같은 오류 signature여도 code/contract가 달라지면 적용조건 경고를 붙인다. 과거 패치를 기계적으로 apply하지 않는다. 과거 권한 부족 사례는 현재 권한을 다시 확인할 신호이지 영구 금지 규칙이 아니다.

## 5. 에이전트로 넘기는 방식

시작 게이트 통과 후 dispatcher가 한 번 초기 검색을 수행해 `retrieval_id`와 note ID 목록을 작업 문맥에 넣는다. 추가 조회는 `/tools/incidents/{id}/cases/search`로 하며 기본 도구 예산 안에서 처리한다. 조회를 한 번 강제한다고 조사 순서나 가설을 강제하는 것은 아니다.

```json
{
  "schema_version": "linemedic.v4",
  "request_id": "REQ-CASE-1",
  "data": {
    "retrieval_id": "RET-001",
    "mode": "memory_assisted",
    "snapshot_id": "MEM-001",
    "engine": "sqlite_fts5",
    "status": "OK",
    "hits": [{
      "note_id": "CASE-OLD-R1",
      "outcome": "VERIFIED_FAILURE",
      "phase": "verification",
      "summary": "예외만 없애고 집계값을 기본값으로 반환한 시도는 업무 검사에서 실패",
      "applicability_warning": "현재 코드·입력 조건에 맞는지 다시 확인해야 함",
      "evidence_id": "EV-HISTORY-001",
      "source_ref": {"run_id": "run-old", "verification_id": "VER-OLD"}
    }]
  },
  "evidence_ids": ["EV-HISTORY-001"]
}
```

history note는 원본 run에 속하지만 현재 incident가 인용할 수 있도록 **권한·snapshot을 확인한 read projection**에 새 evidence ID를 만든다. 과거 사건 evidence ID를 그대로 current proposal에 섞지 않는다. 원본 note ID·hash·source event는 projection에 남긴다. 이전 계약의 ‘동일 incident evidence만 인용’과 충돌하지 않는다.

자료 자체는 비신뢰 텍스트다. 과거 댓글/노트 안의 ‘이 검사기를 끄라’, URL, 임의 수신자, 지시는 실행 규칙으로 승격하지 않는다. 저장된 정확한 관측과 해석을 분리한다. case 조회가 권한을 부여하거나 완료 게이트를 생략하지 않는다.

## 6. cold_start와 memory_assisted 평가 분리

| 모드 | 과거 사례 | 용도 |
|---|---|---|
| `cold_start` | case 검색 결과 비움, 정적 승인 매뉴얼만 제공 | 과거 정답 유출 없는 기본 능력 평가 |
| `memory_assisted` | 시작 전에 고정한 corpus snapshot·cutoff 이내 기록만 | 정답·오답노트 재사용의 효과와 위험 평가 |

snapshot에는 허용 note ID/revision/hash/created_at, 선택 기준, seed 여부를 명시한다. 실행 도중 생긴 자신의 성공·미래 verification 결과는 해당 snapshot에 넣지 않는다. 현재 holdout fixture·정답표는 어떤 모드에서도 에이전트에 제공하지 않는다.

평가는 비교 가능한 bug 변형에 같은 모델·예산·권한을 적용하고, cold와 memory를 별도 결과표로 남긴다. 같은 patch를 읽었으면 ‘새 문제 일반화’가 아니라 해당 기록의 재사용 시험이라고 설명한다. corpus의 미실행 seed, human patch, agent run은 서로 구분한다. 실행 실패나 검색 no-hit도 제외하지 않는다.

필수 확인: 관련 정답 인용, 관련 오답의 실패 조건 인용, PR-only가 성공으로 승격되지 않음, 권한 차단을 오답으로 오분류하지 않음, 다른 repo 기록 비노출, RETRACTED/미래 노트 제외, stale source 재검증, 주입된 지시 무시/권한 경계 유지.

## 7. 저장·정제·보존

public Issue·메일·RAG에는 정제된 최소 요약만 보낸다. raw 증거는 기존 run artifact 경로·보존 정책에 두고 임의 외부 링크로 연결하지 않는다. token·고객정보·환경변수·로그의 자유 입력은 색인 전에 제거·마스킹한다. 정제 실패 시 PUBLISHED로 올리지 않는다.

case note 원본은 append revision 방식, 검색 인덱스는 재구축 가능한 파생물이다. reset은 과거 notes를 삭제하지 않는다. 대신 현재 run의 memory mode/snapshot을 초기화해 평가 오염을 막는다. 보존 기간·삭제 권한은 운영 적용 전 별도 확정하며 ‘영구 보존’을 기본값으로 단정하지 않는다.

## 8. P1과 중단선

Nemotron embedding, vector DB, reranker, long-term skill 자동 작성은 core 이후다. vector DB가 없어도 **검색 → 모델에 근거 제공 → 실제 판단에서 인용**이 연결되면 요청한 사례 기반 보조 흐름을 시연할 수 있다. 반대로 파일만 쌓아 놓거나 검색 결과를 모델이 받지 않았다면 RAG가 동작했다고 부르지 않는다.

정답/오답 노트를 많이 만드는 것보다 **틀린 판정을 정답으로 저장하지 않는 것**이 우선이다. 이전 오류의 반복 감소·시간 단축 수치는 실제 비교 전 주장하지 않는다.
