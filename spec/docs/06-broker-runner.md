# 06. 브로커·패치 게이트·격리 러너

> v4: v2 검사 단계를 모두 core로 유지하고, GitHub 실행 주체를 봇 계정으로, 결과 불명 재조회를 사람이 하는 CLI(core)와 자동 재조회(hardening H04)로 나눴다. 브로커는 모델이 제안한 행동을 제한된 실제 조치로 바꾸는 모듈이다. **원인 해석의 완전한 판별기나 악성 코드 탐지 제품이 아니다.** `create_pr`, `create_work_order_draft`, `escalate` 세 액션만 P0에 둔다.

## 1. 공통 처리

| 단계 | 검사 | 실패 시 |
|---|---|---|
| B01 | principal·run·incident·attempt·예산 | 접수 거부 또는 이관 |
| B02 | schema·category/action 매핑·필수 근거 | 거절, 수정 예산이 남으면 1회만 |
| B03 | 증거가 같은 run·사건·등록 출처에 속하는지 | `EVIDENCE_SCOPE_MISMATCH` |
| B04 | 외부 execution intent·기존 PR·초안 유무 | 기존 결과 반환 또는 충돌 |
| B05 | 텍스트/패치의 민감 값·허용 채널 검사 | `SENSITIVE_CONTENT` |
| B06 | 실제 액션 전 상태·version 재확인 | `STATE_CONFLICT` |

증거 실존 검사는 인용 대상을 확인할 뿐 인과관계를 보증하지 않는다. 단순 secret prefix 검사는 알려진 패턴을 줄이는 보조 기능이다. 모든 비밀·인코딩된 정보 유출을 탐지한다고 주장하지 않는다. 실제 데이터는 합성 자료만 사용한다.

## 2. PR 수정 범위

| 항목 | P0 정책 |
|---|---|
| 업무 코드 | 정확히 `app/defects.py` 수정 허용 |
| 재현 테스트 | `tests/repro/test_*.py`에 **새 파일 하나 추가** |
| 파일 개수 | 최대 2개 |
| 줄 수 | additions + deletions 합계 100 이하, header 제외 |
| 기존 테스트 | 수정·삭제 금지, `tests/regression/**` hash 보호 |
| 설정 | Dockerfile, 의존성, `.github`, pytest 설정, conftest, 인증·infra·policy 변경 금지 |
| 파일 타입 | 정규 UTF-8 텍스트만. symlink·submodule·mode 변경·binary·rename 금지 |
| 경로 | 절대경로·`..`·NUL·역슬래시 모호성·경로 정규화 우회 거부 |

문자열 prefix `app/` 검사만 하지 않는다. **실제 patch 적용 후 Git tree의 변경 경로·파일 mode·hash를 다시 확인**한다. checkout은 서버 소유 disposable 경로이며 사용자가 보낸 폴더에 git 명령을 실행하지 않는다.

## 3. base와 candidate 생성

1. 사건 생성 시 관찰한 배포 base SHA와 run의 허용 base SHA를 비교한다.
2. target baseline 브랜치가 아직 해당 base를 가리키는지 확인한다. 다른 사람이 바꿨으면 중단한다.
3. 신뢰된 저장소 mirror에서 base의 깨끗한 사본을 만든다. credential·hook·로컬 global Git 설정을 테스트 환경에 가져가지 않는다.
4. `git apply --check` 후 patch를 적용한다. 원본 diff의 SHA-256, 최종 변경 tree, 허용 파일 목록을 기록한다.
5. 서버가 새 candidate commit을 만들고 `candidate_sha`, `candidate_tree`를 기록한다. 에이전트가 제공한 candidate ID를 신뢰하지 않는다.
6. 단계별 검사를 수행한다. 실제 실행 없이 생성한 보고서를 통과로 취급하지 않는다.

Git 명령은 고정 argv로 호출한다. PR title·branch·diff·파일명 같은 비신뢰 문자열을 shell template으로 조합하지 않는다.

## 4. 세 종류의 검사

### R0. 환경·기존 기준 확인

고정 runner image와 보호된 pytest 설정을 사용한다. 기존 회귀 테스트를 base에서 실행해 환경 자체가 정상인지 확인한다. base는 의도된 새 입력에서만 버그가 나며 기존 정상 입력 테스트는 통과해야 한다.

### R1. 재현: base + 신규 테스트만

`app/defects.py`는 base 그대로 둔 채 새 테스트만 추가한다. 테스트가 수집되고 실제 실행돼 **의도한 업무 실패를 드러내는지** 확인한다.

| 관찰 | 판정 |
|---|---|
| pytest 1, test가 실행됐고 예상 KeyError/업무 assertion 실패 | 재현 후보로 인정 |
| pytest 0 | 재현 실패: 기준 코드에서도 통과 |
| pytest 2/3/4/5 및 알려지지 않은 종료 상태 | interruption/internal/usage/no-tests 등, 재현 증거 아님 |
| setup·import·수집 오류, OOM, runner timeout | 환경 또는 실행 실패 |
| xfail·skip만 있거나 항상 실패하는 무관 assertion | 유효한 재현으로 인정하지 않음 |

**구현 방법 `[core]`:** runner가 신뢰 경로에 `--junitxml`을 쓰게 하고, supervisor가 종료 코드와 junit 결과를 함께 읽는다. R1은 "종료 코드 1 + 수집된 테스트 1개 이상 + failure 1개 이상 + error 0개"일 때만 재현 후보로 인정한다. R2는 "종료 코드 0 + 새 테스트와 보호 회귀가 모두 passed"일 때만 통과다. 그 밖의 조합은 모두 재현·통과 증거가 아니다.

pytest 공식 문서는 테스트 실패와 내부 오류·명령 오류·미수집 등을 구분한다. 0이 아닌 종료 코드를 모두 재현 성공으로 해석하지 않는다. 버전에 추가 종료 코드가 있으면 허용되지 않은 값은 안전 중단한다. [공식 출처 W05](14-decisions-sources.md#w05)

에러 문자열 일치만으로 테스트의 의미가 증명되지는 않는다. 관찰된 입력·stack·업무 규칙과 연결됐는지는 PR에서 사람에게 보여준다.

### R2. candidate 회귀

신규 재현 테스트 통과와 기존 보호 회귀 테스트 통과를 **별도로** 기록한다. agent가 기존 테스트·수집 설정을 바꾸지 않았는지 hash·tree로 확인한다. runner가 구성한 고정 명령을 사용하고 repo 설정·plugin 자동 로드를 허용하지 않는 실행 profile을 만든다.

테스트 기준 파일이 read-only여도 같은 Python 프로세스에서 실행되는 비신뢰 코드의 모든 조작을 배제하지 못한다. **배포 후 외부 HTTP 업무 검증은 독립 프로세스에서 별도로 수행**한다. 테스트 PASS는 악성 코드가 없다는 보장이 아니다.

## 5. runner 실행 프로필

Docker 문서는 비특권 실행, capability 축소, Docker daemon 권한과 자원 제한을 별도 고려사항으로 다룬다. 아래 자원값과 구성은 LineMedic의 예선용 설계 결정이다. [공식 출처 W07·W08](14-decisions-sources.md#w07)

| 항목 | 설정 목표 |
|---|---|
| 이미지 | 의존성을 사전 설치한 신뢰 image ID 고정 |
| 네트워크 | none; GitHub·모델·Control API·인터넷 접근 없음 |
| 사용자 | non-root UID/GID, 모든 불필요 capability 제거 |
| filesystem | root와 checkout read-only, `/tmp`만 크기 제한 tmpfs |
| 환경 | 허용한 최소 변수만. `PYTHONPATH`, pytest plugin·config는 호스트가 구성 |
| privilege | privileged·host network·host PID·Docker socket·host home 금지 |
| 자원 | 1 CPU, 512 MiB, PID 64, 단계당 60초, 로그 1 MiB 상한 |
| 종료 | timeout 시 전체 컨테이너 종료·제거; 프로세스 일부만 방치하지 않음 |
| 결과 | supervisor가 container ID·image ID·exit/OOM/timeout·raw log를 기록 |

Docker run 실패와 pytest 실패를 구분한다. Docker의 125/126/127은 Docker/호출 관련 실패일 수 있다. [공식 출처 W08](14-decisions-sources.md#w08)

P0는 컨테이너를 만드는 일반 API를 제공하지 않는다. runner image, mount source, test argv, limits는 전부 서버 카탈로그에서 온다. 비신뢰 JUnit/텍스트 결과는 크기 제한·안전 파싱을 적용한다.

## 6. GitHub 실행과 멱등성

- 저장소는 팀 조직의 `l3-mes-api` 한 개를 config에 고정한다.
- PR은 **GitHub App 또는 전용 봇 계정**의 최소 권한 credential로 연다. 팀원 개인 PAT를 쓰지 않는다. PR 작성자는 자기 PR을 승인할 수 없기 때문이다([02](02-architecture.md)의 GitHub 권한 구성, [W11](14-decisions-sources.md#w11)).
- 머지 방식은 squash 하나만 허용한다.
- target: `baseline/<run_id>`. source: `autofix/<run_id>/<incident_id>/<proposal_id>`.
- 브로커 candidate commit을 source branch에 push하고 PR head가 그 SHA인지 읽어서 확인한다.
- PR title/body는 plain text template로 만든다. 증거·검사·변경·한계를 넣되 비밀과 내부 경로는 정제한다.
- 리뷰 없이 머지·base 직접 변경을 허용하지 않는다. 적용한 GitHub 보호 규칙과 credential의 실제 우회 여부는 스파이크에서 시험한다.
- v4는 연결 Issue에 단계별 댓글 알림을 남긴다. 알림 outbox는 [16](16-notifications.md)을 따른다. 이미 있는 동일 PR의 reference를 반환한다.

외부 호출 전에 execution intent와 논리 키를 저장한다. branch 생성 후 PR 생성이 timeout되면 `UNKNOWN`을 남긴다. reconcile은 정확한 head/base/marker/candidate를 조회한다. 존재하지 않는다고 한 번 조회된 것만으로 즉시 재생성하지 않는다. 실제 결과가 끝내 불명확하면 운영자에게 이관한다.

| 등급 | reconcile 방식 |
|---|---|
| `[core]` | 운영자가 `make reconcile EXECUTION_ID=...`로 GitHub 상태를 조회하고 결과를 기록한다. 새 PR을 만들지 않는다 |
| `[hardening H04]` | 제한 횟수·간격의 자동 재조회 후 확인된 상태를 기록한다 |

GitHub branch protection은 stale approval 폐기와 최신 변경 리뷰 조건을 제공한다. 실제 사용 가능성과 적용 상태를 확인해야 한다. [공식 출처 W04](14-decisions-sources.md#w04)

## 7. 정비 요청 초안

정비 초안 자체의 CMMS·현장 지시 전송은 하지 않는다. 연결된 GitHub Issue에는 검토 필요 알림을 남긴다. 서버 catalog의 equipment와 manual reference를 확인한 뒤 execution.result에 초안을 저장하고 `WORK_ORDER_DRAFTED`로 전이한다. 초안에는 다음만 포함한다.

`equipment_id`, `symptom`, `probable_cause`, `evidence_ids`, `manual_ref_id`, `open_questions`, `review_required: true`, `delivery_status: not_sent`.

여기서 `delivery_status`는 **실제 CMMS/현장 작업지시 전송**만 뜻한다. GitHub의 `HANDOFF_DRAFTED` 알림 접수 상태는 notifications에 별도로 기록한다.

관찰 사실과 가설을 분리한다. 렌즈 청소·램프 교체·제어값 변경을 모델 문장 그대로 작업자에게 명령하지 않는다. 형식이 허용됐다는 사실을 현장 안전 검증으로 오해하지 않는다.

## 8. PR 본문 템플릿

```markdown
## LineMedic 수정 제안 — <incident_id>

- 관련 Issue: Related to #<issue_number>
- 작업: <work_id> / generation <generation> / <run_id>
- 대상: <repository> / <baseline_branch>
- 기준 코드: <base_sha>
- 검사한 candidate: <candidate_sha> / <candidate_tree>
- 원인 가설: <root_cause_hypothesis>
- 근거: <evidence ID, 관찰 사실, 시각>
- 변경 파일: <실제 diff 결과>

### 브로커가 관찰한 검사
- 기준 환경·회귀: <결과 ID>
- base + 새 테스트: <예상 실패 또는 실패 이유>
- candidate 새 테스트·보호 회귀: <결과 ID>

### 보장하지 않는 것
재현 테스트는 실제 원인이 반드시 코드라는 증거가 아닙니다.
검사는 악성 코드 부재나 모든 업무 동작을 보장하지 않습니다.
머지 전에 사람이 diff와 근거를 검토해야 합니다.
배포 후 별도 업무 계약 검사 전에는 복구 완료가 아닙니다.
```

## 9. 담당자 완료 기준

잘못된 path·기존 test 변경·테스트 미수집·가짜 재현·unknown 외부 결과를 거절하고, 실제 candidate PR과 검증 기록을 연결한다. 사람의 무관한 수정이 PR에 추가되면 기존 검사 결과를 재사용하지 않는다. 상세 case ID는 [09](09-scenarios-evaluation.md)에 있다.


## 10. v4 lifecycle와 연결

### Issue 생성과 PR 생성의 분리

Issue 연결/신규 생성은 [15](15-issue-intake-workflow.md)의 trusted router가 에이전트 시작 **전에** 처리한다. broker의 모델 액션 목록은 세 개를 유지한다. `create_issue`·`comment_issue`·`send_mail`을 모델이 자유롭게 호출하는 외부 도구로 추가하지 않는다.

기존 PR 생성 검사 전에 work가 해당 Issue에 연결됐는지, start receipt가 확인됐는지, 현재 repo/Issue 승인 snapshot이 유효한지 검사한다. 현재 Issue state·assignee·기존 PR 충돌은 외부 생성 직전 재조회한다. 동일 work/candidate의 PR만 재사용하며 타인의 PR 브랜치를 덮어쓰지 않는다.

### 결과 이벤트

PR 생성 receipt가 확인되면 incident PR_OPENED/work WAITING_REVIEW와 `PR_READY` outbox intent를 같은 transaction에서 기록한다. 정비 초안은 WORK_ORDER_DRAFTED/HANDED_OFF와 `HANDOFF_DRAFTED`, 진행 불가는 ESCALATED/BLOCKED와 `WORK_BLOCKED`다. 알림 provider 호출을 DB transaction 안에서 수행하지 않는다.

PR 본문에 parent Issue, work generation, run/incident, base/candidate, 검사 ID를 연결한다. `Related to #42`처럼 중립적인 관계 표현을 사용하고 검증 전 auto-closing keyword를 넣지 않는다. 자동 Issue close도 core에서 하지 않는다.

### 실패 기록과 사례

각 검사·실패·PR 준비 event는 source_event_key를 가진 case builder 입력이 된다. regression/test FAIL은 단계별 관찰, policy 거부는 BLOCKED, PR-only는 UNVERIFIED다. 실제 업무 verifier PASS 없이 VERIFIED_SUCCESS를 생성하지 않는다. 브로커 거절을 모두 '틀린 코드'라고 요약하지 않는다.

Issue 생성·댓글·PR 응답이 불명확하면 신규 요청을 반복하기 전에 해당 intent의 외부 identity를 조회한다. nonce/marker만 맞는 다른 작성자의 리소스를 채택하지 않는다. 기본은 운영자가 reconcile, 자동 bounded 재조회는 hardening이다.
