"""JSON Lines 로그를 stdout에 남긴다.

한 줄에 한 이벤트이고 필드는 고정이다:
ts, level, service, event, request_id, lot_id, path, status,
error_type, error_field, top_frame, top_frame_line, stack
예외 정보에는 절대 경로를 넣지 않고 `app.<모듈>:<함수>` 형식만 쓴다. 환경 변수는 기록하지 않는다.
"""

import json
import sys
import traceback
from datetime import UTC, datetime
from pathlib import Path

SERVICE = "mes-api"
FIELDS = (
    "ts",
    "level",
    "service",
    "event",
    "request_id",
    "lot_id",
    "path",
    "status",
    "error_type",
    "error_field",
    "top_frame",
    "top_frame_line",
    "stack",
)
APP_DIR = Path(__file__).resolve().parent
MAX_STACK_FRAMES = 10


def utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _app_module(filename: str) -> str | None:
    try:
        relative = Path(filename).resolve().relative_to(APP_DIR)
    except ValueError:
        return None
    return ".".join(("app", *relative.with_suffix("").parts))


def exception_fields(exc: BaseException) -> dict:
    frames = []
    for frame in traceback.extract_tb(exc.__traceback__):
        module = _app_module(frame.filename)
        if module is not None:
            frames.append((module, frame.name, frame.lineno))
    top = frames[-1] if frames else None
    error_field = None
    if isinstance(exc, KeyError) and exc.args and isinstance(exc.args[0], str):
        error_field = exc.args[0]
    return {
        "error_type": type(exc).__name__,
        "error_field": error_field,
        "top_frame": f"{top[0]}:{top[1]}" if top else None,
        "top_frame_line": top[2] if top else None,
        "stack": [f"{m}:{n}:{line}" for m, n, line in frames[-MAX_STACK_FRAMES:]],
    }


def log_event(level: str, event: str, stream=None, **fields) -> dict:
    record = dict.fromkeys(FIELDS)
    record.update(ts=utc_now(), level=level, service=SERVICE, event=event)
    record.update({key: value for key, value in fields.items() if key in FIELDS})
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
    print(line, file=stream or sys.stdout, flush=True)
    return record
