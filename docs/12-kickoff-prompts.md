# 12. 코딩 에이전트에게 줄 프롬프트

> 사용자가 `lineMedic/`에서 코딩 에이전트(Claude Code 등)를 열고 그대로 붙여 넣는 문장이다. 에이전트는 [AGENTS.md](../AGENTS.md)를 자동으로(Claude Code는 CLAUDE.md를 통해) 읽는다.

## 1. 처음 시작

```text
AGENTS.md와 STATUS.md를 읽고 LineMedic v4 구현을 시작해.
먼저 docs/08-task-plan.md §2에 따라 내가 준비해야 할 사람 게이트(G1, G2, G3, G5, G6, G11)를
docs/10-human-gates.md 형식으로 한 번에 정리해 보여 주고 STATUS.md 사람 게이트 표에 요청 내용과 시각을 직접 기록해.
그다음 tasks/B00-bootstrap.md부터 순서대로 진행해.
카드 하나가 끝날 때마다 STATUS.md의 작업표·완료 보고·현재 작업·다음 작업을 갱신하고,
docs/11의 기록 점검으로 테스트 결과·원본 증거·게이트를 확인한 뒤 커밋해.
```

## 2. 이어서 진행

```text
STATUS.md를 읽고 "현재 작업" 또는 실행 순서표의 다음 카드를 이어서 진행해.
게이트에 막히면 STATUS.md에 BLOCKED_ON_HUMAN과 남은 일·재개 조건을 적고 현재 작업을 비워.
AGENTS.md §2의 조건을 만족하는 다음 카드의 게이트 없이 가능한 부분으로 넘어가.
```

## 3. 특정 카드만

```text
tasks/W25-work-lifecycle.md 카드만 구현해.
카드의 원본 근거 절만 읽고, 수용 기준의 테스트를 먼저 작성한 뒤 구현해.
구현은 카드의 파일 범위 안에서 하고, STATUS.md의 진행 상태와 완료 보고를 함께 갱신해.
끝나면 완료 보고 양식으로 알려 줘.
```

## 4. 게이트를 열었을 때

```text
G2를 열었어. .env에 GITHUB_BROKER_CREDENTIAL, GITHUB_SETUP_CREDENTIAL을 넣었고
GITHUB_REPOSITORY=<org>/l3-mes-api, GITHUB_REPOSITORY_ID=<숫자>, 리뷰어는 <계정>이야.
G10(쓰기 활성화)도 허용해. 전용 데모 repo에서만 써.
STATUS.md의 G2·G10에 DONE과 완료 시각·승인 범위를 기록해.
대기 중이던 카드의 선행·나머지 게이트를 다시 확인한 뒤 가능한 live 검증을 순서대로 진행해.
```

## 5. 시간이 부족할 때

```text
남은 시간이 <N>시간이야. docs/08-task-plan.md §6 기준으로
"줄이면 요청 미충족"인 항목을 우선하고, 지금 상태에서 끝낼 수 있는 범위와
포기할 항목을 STATUS.md의 해당 카드 메모와 다음 작업에 적고 그 순서로 진행해.
```

## 6. 점검 요청

```text
지금까지 STATUS.md에 LIVE_VERIFIED로 적힌 항목의 증거 경로를 하나씩 확인하고,
증거가 없거나 mock 결과인 항목을 찾아 상태를 바로잡아 줘.
```

## 7. 제출 전

```text
tasks/W21-readme-submission.md의 점검 목록으로 README와 신청서 초안을 검사해.
docs/11-definition-of-done.md §4의 금지 표현이 있으면 실제 증거에 맞게 고쳐.
```
