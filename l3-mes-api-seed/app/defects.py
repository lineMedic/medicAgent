"""로트별 불량 집계."""


def summarize(lot_id: str, records: list[dict]) -> dict:
    by_inspector: dict[str, int] = {}
    for row in records:
        inspector = row["inspector_id"]
        by_inspector[inspector] = by_inspector.get(inspector, 0) + 1
    return {
        "lot_id": lot_id,
        "total_defects": len(records),
        "by_inspector": by_inspector,
    }
