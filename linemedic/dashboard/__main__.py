"""읽기 전용 대시보드 (W18, D49·D85): `python -m linemedic.dashboard [--db] [--run-id] [--port]`.

- `127.0.0.1`에만 bind한다. 다른 주소를 고르는 옵션이 없다
- SQLite를 `file:...?mode=ro` URI와 `PRAGMA query_only`로 연다. 쓰기는 실패한다
- `GET /`(HEAD 포함)만 있다. 쓰기 route·JavaScript·token 저장·링크 자동 호출이 없다
- Jinja2 autoescape로 서버에서 렌더링한다. 응답의 CSP가 script를 전부 막는다(escape 실패 대비)
"""

import argparse
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import jinja2
import uvicorn
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import HTMLResponse
from starlette.routing import Route

from linemedic.common.clock import to_rfc3339
from linemedic.common.config import DEFAULT_ENV_FILE, process_env
from linemedic.common.ids import is_valid_run_id
from linemedic.control_plane import runs
from linemedic.control_plane.store import Tx
from linemedic.dashboard import readmodel

BIND_HOST = "127.0.0.1"
DEFAULT_PORT = 8090
TEMPLATES = Path(__file__).resolve().parent / "templates"
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'unsafe-inline'; img-src 'none'; form-action 'none';"
        " frame-ancestors 'none'; base-uri 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


def connect_readonly(path: Path) -> sqlite3.Connection:
    """읽기 전용 연결. 파일이 없으면 만들지 않고 실패한다."""
    if not path.is_file():
        raise FileNotFoundError(str(path))
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def environment() -> jinja2.Environment:
    return jinja2.Environment(
        loader=jinja2.FileSystemLoader(TEMPLATES),
        autoescape=True,
        undefined=jinja2.StrictUndefined,
    )


def load_model(db_path: Path, run_id: str | None = None, now: datetime | None = None) -> Any:
    now = now or datetime.now(UTC)
    conn = connect_readonly(db_path)
    try:
        conn.execute("BEGIN")  # 한 시점의 일관된 값
        try:
            return readmodel.build(Tx(conn, to_rfc3339(now)), run_id, now=now)
        finally:
            conn.execute("ROLLBACK")
    finally:
        conn.close()


def render(model: Any) -> str:
    return environment().get_template("index.html").render(model=model)


def create_app(db_path: Path, run_id: str | None = None) -> Starlette:
    async def index(request: Request) -> HTMLResponse:
        model = await run_in_threadpool(load_model, db_path, run_id)
        return HTMLResponse(render(model), headers=SECURITY_HEADERS)

    return Starlette(routes=[Route("/", index, methods=["GET"])])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m linemedic.dashboard")
    parser.add_argument("--db", type=Path, help="제어 DB (기본: <RUNS_DIR 또는 runs>/linemedic.db)")
    parser.add_argument("--run-id", help="보여 줄 run (기본: 활성 run, 없으면 가장 최근 run)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    args = parser.parse_args(argv)
    db_path = args.db or runs.default_db_path(process_env(args.env_file))
    if not db_path.is_file():
        print(f"dashboard 실패: 제어 DB가 없다: {db_path} (먼저 make run-new)", file=sys.stderr)
        return 2
    if args.run_id is not None and not is_valid_run_id(args.run_id):
        print(f"dashboard 실패: run_id 형식이 아니다: {args.run_id}", file=sys.stderr)
        return 2
    if not 1 <= args.port <= 65535:
        print(f"dashboard 실패: port 범위가 아니다: {args.port}", file=sys.stderr)
        return 2
    print(f"http://{BIND_HOST}:{args.port}/ (읽기 전용, {db_path})", file=sys.stderr)
    app = create_app(db_path, args.run_id)
    uvicorn.run(app, host=BIND_HOST, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
