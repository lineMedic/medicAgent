"""검증 대상 컨테이너 관찰 (W05 1부, spec 08 §5 core observer).

- t0부터 대상 컨테이너의 로그 스트림을 끊김 없이 읽는다. 스트림이 끝나거나 읽을 수 없으면 gap이다.
- 호출될 때마다 host에서 container ID·image ID를 inspect해 시작 값과 비교한다.
- 같은 오류 signature(오류 종류·위치·경로)의 재발을 센다.

앱이 보고하는 상태를 믿지 않는다. identity는 host inspect로만 확인한다.
로그를 숨길 수 있으므로 로그 관찰만으로 정상 판단하지 않는다.
판정은 verifier가 표본 응답과 함께 한다.
"""

import json
from dataclasses import dataclass
from typing import Any

from linemedic.integrations.docker import DockerPort, LogStream


class ObserverError(RuntimeError):
    """관찰을 시작할 수 없음."""


@dataclass(frozen=True)
class RecurrenceSignature:
    """재발 판정에 쓰는 오류 signature. 줄 번호·요청 ID·로트 ID처럼 불안정한 값은 넣지 않는다."""

    error_type: str
    top_frame: str
    path: str

    def matches(self, event: dict[str, Any]) -> bool:
        return (
            event.get("error_type") == self.error_type
            and event.get("top_frame") == self.top_frame
            and event.get("path") == self.path
        )


@dataclass(frozen=True)
class ObserverStatus:
    stream_gap: bool
    identity_changed: bool
    detail: str | None
    recurrences: int
    lines_seen: int
    non_json_lines: int


class ContainerObserver:
    def __init__(
        self, docker: DockerPort, container: str, signature: RecurrenceSignature | None = None
    ) -> None:
        self.docker = docker
        self.container = container
        self.signature = signature
        self.expected: dict[str, str] | None = None
        self._stream: LogStream | None = None
        self._recurrences = 0
        self._lines = 0
        self._non_json = 0
        self._stopped = False

    def start(self, since: str | None = None) -> dict[str, str]:
        """시작 identity를 기록하고 로그 스트림을 연다. 반환값은 검증 결과에 남긴다."""
        data = self.docker.inspect(self.container)
        if not data:
            raise ObserverError(f"검증 대상 컨테이너가 없다: {self.container}")
        self.expected = {"container_id": data["Id"], "image_id": data["Image"]}
        self._stream = self.docker.logs_follow(self.container, since=since)
        return dict(self.expected)

    def _consume(self) -> None:
        assert self._stream is not None
        for line in self._stream.read_lines():
            if not line.strip():
                continue
            self._lines += 1
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                self._non_json += 1
                continue
            if isinstance(event, dict) and self.signature and self.signature.matches(event):
                self._recurrences += 1

    def poll(self) -> ObserverStatus:
        if self.expected is None or self._stream is None:
            raise ObserverError("start()를 먼저 호출해야 한다")
        self._consume()
        gap = not self._stream.alive() and not self._stopped
        identity_changed, detail = False, None
        data = self.docker.inspect(self.container)
        if not data:
            identity_changed, detail = True, "검증 대상 컨테이너가 사라짐"
        elif data["Id"] != self.expected["container_id"]:
            identity_changed, detail = True, "container ID 변경"
        elif data["Image"] != self.expected["image_id"]:
            identity_changed, detail = True, "image ID 변경"
        if gap and detail is None:
            detail = "로그 스트림이 끊김"
        return ObserverStatus(
            stream_gap=gap,
            identity_changed=identity_changed,
            detail=detail,
            recurrences=self._recurrences,
            lines_seen=self._lines,
            non_json_lines=self._non_json,
        )

    def stop(self) -> None:
        self._stopped = True
        if self._stream is not None:
            self._stream.close()
