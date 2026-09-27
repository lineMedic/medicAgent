"""S1b 거짓 정상 구현 (팀이 주입한 결함, origin=human_injected_negative).

trusted harness 전용이다. 에이전트 성과가 아니며, 제품 broker·/tools에는 이 경로가 없다.
검사자 필드가 없는 record가 있으면 예외 없이 HTTP 200을 돌려주지만 total_defects를 0으로 보고한다.
verifier가 이것을 FAIL/content_mismatch로 거절하는지 시험한다(spec 09 §4).
"""


def summarize(lot_id: str, records: list[dict]) -> dict:
    by_inspector: dict[str, int] = {}
    missing = 0
    for row in records:
        if "inspector_id" not in row:
            missing += 1
            continue
        by_inspector[row["inspector_id"]] = by_inspector.get(row["inspector_id"], 0) + 1
    return {
        "lot_id": lot_id,
        "total_defects": 0 if missing else len(records),
        "by_inspector": by_inspector,
    }
