"""불량 로트 데이터 로더.

`MES_DATA_DIR/lots/<lot_id>.json` 파일을 읽는다.
파일 형식: {"lot_id": "...", "records": [{"defect_id": "...", "inspector_id": "..."}, ...]}
"""

import json
import os
import re
from pathlib import Path

LOT_ID_RE = re.compile(r"^[A-Z0-9][A-Z0-9-]{0,63}$")
DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent / "data"


class InvalidLotId(ValueError):
    """로트 ID 형식이 아님."""


class LotNotFound(LookupError):
    """해당 로트 파일이 없음."""


def data_dir() -> Path:
    return Path(os.environ.get("MES_DATA_DIR") or DEFAULT_DATA_DIR)


def load_lot(lot_id: str) -> list[dict]:
    if not LOT_ID_RE.fullmatch(lot_id):
        raise InvalidLotId(lot_id)
    path = data_dir() / "lots" / f"{lot_id}.json"
    if not path.is_file():
        raise LotNotFound(lot_id)
    document = json.loads(path.read_text(encoding="utf-8"))
    return document["records"]
