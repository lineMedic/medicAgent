-- W27 사례 검색 파생 인덱스 (spec 04 §5.1, D54). FTS5가 없는 SQLite에서는 store.migrate가 이 파일의
-- 문장을 실행하지 않고 적용 기록만 남긴다(검색 엔진은 keyword_fallback).
-- 인덱스는 PUBLISHED case note revision에서 다시 만들 수 있는 파생물이다(POST /ops/cases/rebuild-index).
CREATE VIRTUAL TABLE case_search USING fts5(note_id UNINDEXED, search_text, tokenize='unicode61');
