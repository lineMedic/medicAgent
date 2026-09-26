# LineMedic v4 패키지 문서 검증 결과

> **문서 작성 환경에서 수행한 정적·스키마 검사다.** 제품 서버·GitHub API·메일·모델·sandbox·업무 복구의 실행 결과가 아니다. 구현 작업의 완료 상태는 여전히 NOT_CHECKED/NOT_RUN이다.

## 1. 검토·수정 대상

입력은 `LineMedic_Development_Pack_v3.zip`에서 추출한 Markdown **18개 전부**다. 이전 원안의 요약만으로 v3를 대체하지 않았다. v3 ZIP과 원본 추출 파일은 수정하지 않았다.

- 원본 ZIP SHA-256: `795f863086209fe2f0d00dd50a2adf5cb73bd750f26e8e0edd5efe8333e31543`
- 기존 경로를 유지하고 수정한 문서: **18개**
- 신규 Markdown: **8개** — 검토 보고서, 규범 15~18, 차단/사례 템플릿, 이 검증 결과
- v4 전체 Markdown: **26개**

파일별 v3 hash·실제 원문 위치는 [검토 보고서](V3-REVIEW-AND-V4-CHANGES.md)에 있다. 실제 구현 전환은 [18](docs/18-migration-validation.md)을 따른다.

## 2. 문서 검사

| 검사 | 실제 범위 | 결과 |
|---|---|---|
| UTF-8 MD 로드 | 26개 파일 | PASS |
| fence 쌍 | 34개 코드블록 | PASS |
| 상대 Markdown 링크 | 124개 참조 대상 | PASS |
| 상대 anchor | 위 참조 중 20개 anchor | PASS |
| JSON 코드블록 | 10개 `json.loads` 파싱 | PASS |
| YAML 코드블록 | 3개 `yaml.safe_load` 파싱 | PASS |
| fresh SQL 코드블록 | 2개, SQLite 3.46.1 메모리 DB | PASS |
| v3 원본 보존 | ZIP·문서별 hash를 검토 보고서와 대조 | PASS |
| 기존 문서 경로 보존 | 원래 18개 경로 모두 존재 | PASS |
| ZIP 구조·CRC | MD 26개, 경로 이탈·중복 없음, testzip 오류 없음 | PASS |

외부 HTTP 링크를 전부 재접속하는 link checker는 실행하지 않았다. 출처 14의 새 GitHub·SQLite 기술 문서는 v4 설계 시 별도로 확인했다. Mermaid 코드블록은 fence·문서 구조만 점검했으며 브라우저 다이어그램 렌더링을 자동 검증하지 않았다.

## 3. DDL·FTS5의 실제 확인 항목

정의는 [04](docs/04-data-state.md)의 SQL을 메모리 DB에 실행했다. 아래는 **DDL 제약의 오류 거절과 검색 식의 소규모 확인**이지 서비스 상태 전이·API 구현의 통합 시험이 아니다. 테스트 fixture는 문서 검증용이며 운영 사례나 성능 결과로 사용하지 않는다.

| 번호 | 검사 | 결과 |
|---|---|---|
| 1 | single active run | PASS |
| 2 | active fingerprint unique | PASS |
| 3 | incident FK run | PASS |
| 4 | incident status CHECK | PASS |
| 5 | incident nonnegative count | PASS |
| 6 | single active work per Issue | PASS |
| 7 | work generation unique | PASS |
| 8 | work incident unique | PASS |
| 9 | work Issue FK | PASS |
| 10 | work generation positive | PASS |
| 11 | single global RUNNING work | PASS |
| 12 | API request scope-key unique | PASS |
| 13 | notification logical key unique | PASS |
| 14 | notification state CHECK | PASS |
| 15 | case source event unique | PASS |
| 16 | case revision unique | PASS |
| 17 | case outcome CHECK | PASS |
| 18 | case supersedes FK | PASS |
| 19 | FTS5 lexical query with repo/service/snapshot/retraction filters | PASS |
| 20 | SQLite foreign_key_check empty after fixture inserts | PASS |

`VERIFIED_SUCCESS`를 실제 verifier PASS에만 묶는 cross-row 도메인 검사, 사용자 권한, start receipt와 generation의 결합, schema reject/HTTP409 응답, 외부 실행의 reconciliation은 DDL만으로 완성되지 않는다. 이 제품 로직은 별도 구현·시험이 필요하다.

FTS5 시험은 query syntax, repo/service/snapshot membership·게시 상태 필터를 확인했다. 한국어 검색 품질, 유사 사례 정확도, RAG 효과·모델 성능은 측정하지 않았다.

## 4. 문서 간 의미 일치 점검

기존 Issue 연동 P1·댓글 미지원·자동 알림 제외 문구를 v4 규범에서 갱신하고, 역사 설명·v3 원문 발췌에만 남겼다. claim 경합·같은 멱등키의 다른 본문은 W25 core로 승격했다. v2 wire 언급은 호환성 설명에만 남기고 실제 요청 예시는 v4로 통일했다.

시작 알림으로 바뀌는 단순 updated_at·댓글 수가 승인 snapshot을 무효화하지 않도록 정의했다. 동결된 memory snapshot은 원래 note revision을 읽고 후속 revision으로 바꾸지 않으며, 철회 자료는 제외·기록하도록 맞췄다. 실제 정비 지시 미전송과 GitHub의 ‘정비 요청 초안 생성’ 알림을 별도 상태로 구분했다.

이 점검은 설계 오류가 전혀 없다는 보장은 아니다. 실제 구현 중 충돌을 발견하면 API·상태·관련 수용 시험을 같이 수정한다.

## 5. 이번 작업에서 실행하지 않은 것

| 항목 | 상태 |
|---|---|
| 실제 v3 제품 코드·기존 DB migration | NOT_RUN — 제공된 것은 문서 패키지 |
| GitHub Issue 목록·생성·댓글·polling·author 권한·rate limit | NOT_RUN |
| SMTP 발송·공급자 receipt·사람 수신 | NOT_RUN |
| Nemotron/NAT/NemoClaw/OpenShell SDK와 실제 모델 호출 | NOT_RUN |
| sandbox 정책·생성 코드 격리·Docker 배포 | NOT_RUN |
| 실제 PR·사람 리뷰·exact SHA/image 승인 연결 | NOT_RUN |
| start notification gate·API409·중복 worker 경쟁 | NOT_RUN — DDL 일부 제약만 확인 |
| S1~S7 실제 서비스·모델·provider E2E | NOT_RUN |
| RAG 품질·오답 반복 감소·시간 절감 | NOT_RUN |
| 해커톤 NeMo/Skill API 인정·현재 공식 마감 재확정 | UNCONFIRMED |

문서의 명령·설정 예시는 아직 구현할 인터페이스다. 전체 개발 완료, 자동복구 안전성 보장, 메일 발송 완료로 해석하지 않는다.
