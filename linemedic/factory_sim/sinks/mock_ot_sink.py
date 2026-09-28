"""S3-C 팀 소유 모의 수신 서버 (W17, spec 07 §6, D91).

실제 PLC·설비 대신 요청이 도달했는지만 관찰한다. 제어 프로토콜을 구현하지 않고
아무것도 바꾸지 않는다.

- 모든 HTTP 요청에 204로 답하고, 요청마다 시각·메서드·경로·본문 길이·본문 SHA-256·canary 포함 여부를
  JSON Lines로 남긴다
- 본문 원문은 남기지 않는다(시험이 실패해 진짜 값이 섞여 와도 기록에 남지 않게).
  본문은 64 KiB까지만 읽는다
- 격리 docker network 안에서 `python -m linemedic.factory_sim.sinks.mock_ot_sink`로 띄우거나
  테스트에서 `start()`로 같은 프로세스에 띄운다
"""

import argparse
import hashlib
import json
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MAX_BODY_BYTES = 65536


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class _Sink(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], record: Path, canary: str) -> None:
        super().__init__(address, _Handler)
        self.record = record
        self.canary = canary.encode("utf-8")
        self.lock = threading.Lock()


class _Handler(BaseHTTPRequestHandler):
    server: _Sink

    def _receive(self) -> None:
        length = min(int(self.headers.get("Content-Length") or 0), MAX_BODY_BYTES)
        body = self.rfile.read(length) if length > 0 else b""
        entry = {
            "at": _now(),
            "method": self.command,
            "path": self.path.split("?", 1)[0][:256],
            "length": len(body),
            "sha256": hashlib.sha256(body).hexdigest(),
            "canary_seen": bool(self.server.canary)
            and self.server.canary in body + self.path.encode(),
        }
        with self.server.lock, self.server.record.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self.send_response(204)
        self.end_headers()

    do_GET = do_POST = do_PUT = _receive

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 — 표준 서명
        return None  # 요청 줄(경로·주소)을 stderr에 남기지 않는다. 기록은 record 파일 하나다


def start(host: str, port: int, record: Path, *, canary: str) -> ThreadingHTTPServer:
    """sink를 background thread로 띄운다. 호출자가 `shutdown()`·`server_close()`로 닫는다."""
    record.parent.mkdir(parents=True, exist_ok=True)
    server = _Sink((host, port), record, canary)
    threading.Thread(target=server.serve_forever, name="mock-ot-sink", daemon=True).start()
    return server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="S3-C 팀 소유 모의 수신 서버")
    parser.add_argument("--host", default="0.0.0.0")  # noqa: S104 — 격리 network 안의 컨테이너 전용
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--record", type=Path, required=True)
    parser.add_argument("--canary", required=True)
    args = parser.parse_args(argv)
    args.record.parent.mkdir(parents=True, exist_ok=True)
    _Sink((args.host, args.port), args.record, args.canary).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
