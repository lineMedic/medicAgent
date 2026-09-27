"""Docker 연동 port (D47).

- `DockerPort`: 제어 평면이 쓰는 최소 인터페이스
- `CliDocker`: `docker` CLI를 고정 argv 리스트로 호출한다(shell=False, timeout)
- `FakeDocker`: 테스트용 메모리 구현

정리는 호출자가 넘긴 정확한 이름만 대상으로 한다.
`docker system prune` 같은 범용 삭제는 만들지 않는다.
"""

import copy
import json
import queue
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


class DockerError(RuntimeError):
    """Docker 명령이 실패함. `returncode`는 docker CLI 종료 코드(125/126/127 구분용)."""

    def __init__(self, message: str, returncode: int | None = None) -> None:
        super().__init__(message)
        self.returncode = returncode


class LogStream(Protocol):
    def read_lines(self) -> list[str]: ...

    def alive(self) -> bool: ...

    def close(self) -> None: ...


class DockerPort(Protocol):
    def inspect(self, name: str) -> dict[str, Any] | None: ...

    def image_id(self, ref: str) -> str | None: ...

    def logs_follow(self, name: str, since: str | None = None) -> LogStream: ...

    def logs_once(self, name: str, timestamps: bool = False) -> list[str]: ...

    def run(self, options: list[str], image: str, command: list[str] | None = None) -> str: ...

    def wait(self, name: str, timeout: float) -> int | None: ...

    def logs_capped(self, name: str, max_bytes: int) -> tuple[bytes, bool]: ...

    def exec(self, name: str, command: list[str], timeout: float = 30.0) -> CommandResult: ...

    def stop(self, name: str) -> CommandResult: ...

    def build(
        self, context: Path, dockerfile: Path, tag: str, build_args: dict[str, str] | None = None
    ) -> str: ...

    def network_create(self, name: str, labels: dict[str, str]) -> CommandResult: ...

    def network_remove(self, name: str) -> CommandResult: ...


# ── CLI 구현 ───────────────────────────────────────────────────


class _CliLogStream:
    """`docker logs --follow`의 stdout을 백그라운드 thread로 읽는다."""

    def __init__(self, argv: list[str]) -> None:
        # 비신뢰 컨테이너가 잘못된 UTF-8을 써도 reader가 죽지 않게 대체 문자로 읽는다.
        self._proc = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        self._lines: queue.Queue[str] = queue.Queue()
        self._reader = threading.Thread(target=self._pump, daemon=True)
        self._reader.start()

    def _pump(self) -> None:
        assert self._proc.stdout is not None
        for line in self._proc.stdout:
            self._lines.put(line.rstrip("\n"))

    def read_lines(self) -> list[str]:
        lines = []
        while True:
            try:
                lines.append(self._lines.get_nowait())
            except queue.Empty:
                return lines

    def alive(self) -> bool:
        """process와 reader thread가 모두 살아 있어야 읽는 중이다. 하나라도 끝나면 관찰 공백이다."""
        return self._proc.poll() is None and self._reader.is_alive()

    def close(self) -> None:
        if self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()


class CliDocker:
    def __init__(self, binary: str = "docker", timeout: float = 120.0) -> None:
        self.binary = binary
        self.timeout = timeout

    def _run(self, args: list[str], timeout: float | None = None) -> CommandResult:
        try:
            proc = subprocess.run(
                [self.binary, *args],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout or self.timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return CommandResult(124, "", "timeout")
        return CommandResult(proc.returncode, proc.stdout, proc.stderr)

    def inspect(self, name: str) -> dict[str, Any] | None:
        result = self._run(["inspect", name])
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout or "[]")
        return data[0] if data else None

    def image_id(self, ref: str) -> str | None:
        result = self._run(["image", "inspect", "--format", "{{.Id}}", ref])
        return result.stdout.strip() if result.returncode == 0 else None

    def logs_follow(self, name: str, since: str | None = None) -> LogStream:
        argv = [self.binary, "logs", "--follow"]
        if since:
            argv += ["--since", since]
        return _CliLogStream([*argv, name])

    def logs_once(self, name: str, timestamps: bool = False) -> list[str]:
        """컨테이너 stdout 로그를 지금까지 한 번 읽는다(stderr는 읽지 않는다).

        `timestamps`면 각 줄 앞에 Docker daemon 수신 시각(RFC3339Nano)이 붙는다.
        """
        result = self._run(["logs", *(["--timestamps"] if timestamps else []), name])
        if result.returncode != 0:
            raise DockerError(f"docker logs 실패: {result.stderr.strip()[:300]}")
        return result.stdout.splitlines()

    def run(self, options: list[str], image: str, command: list[str] | None = None) -> str:
        result = self._run(["run", "--detach", *options, image, *(command or [])])
        if result.returncode != 0:
            raise DockerError(f"docker run 실패: {result.stderr.strip()[:300]}", result.returncode)
        return result.stdout.strip()

    def wait(self, name: str, timeout: float) -> int | None:
        """컨테이너가 끝날 때까지 기다려 종료 코드를 돌려준다. `timeout`초를 넘기면 None."""
        result = self._run(["wait", name], timeout=timeout)
        if (result.returncode, result.stderr) == (124, "timeout"):
            return None
        if result.returncode != 0:
            raise DockerError(f"docker wait 실패: {result.stderr.strip()[:300]}", result.returncode)
        return int(result.stdout.strip().splitlines()[-1])

    def logs_capped(self, name: str, max_bytes: int) -> tuple[bytes, bool]:
        """stdout·stderr 로그를 `max_bytes`까지만 읽는다. (내용, 잘림 여부)."""
        proc = subprocess.Popen(
            [self.binary, "logs", name], stdout=subprocess.PIPE, stderr=subprocess.STDOUT
        )
        assert proc.stdout is not None
        try:
            data = proc.stdout.read(max_bytes + 1)
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=10)
            proc.stdout.close()
        return data[:max_bytes], len(data) > max_bytes

    def exec(self, name: str, command: list[str], timeout: float = 30.0) -> CommandResult:
        return self._run(["exec", name, *command], timeout=timeout)

    def stop(self, name: str) -> CommandResult:
        return self._run(["rm", "--force", name])

    def build(
        self, context: Path, dockerfile: Path, tag: str, build_args: dict[str, str] | None = None
    ) -> str:
        args = ["build", "-f", str(dockerfile), "-t", tag]
        for key, value in sorted((build_args or {}).items()):
            args += ["--build-arg", f"{key}={value}"]
        result = self._run([*args, str(context)], timeout=900)
        if result.returncode != 0:
            raise DockerError(f"docker build 실패: {result.stderr.strip()[-500:]}")
        image = self.image_id(tag)
        if image is None:
            raise DockerError(f"빌드한 이미지를 찾을 수 없음: {tag}")
        return image

    def network_create(self, name: str, labels: dict[str, str]) -> CommandResult:
        args = ["network", "create", "--internal"]
        for key, value in sorted(labels.items()):
            args += ["--label", f"{key}={value}"]
        return self._run([*args, name])

    def network_remove(self, name: str) -> CommandResult:
        return self._run(["network", "rm", name])


# ── 테스트용 구현 ──────────────────────────────────────────────


@dataclass
class FakeLogStream:
    lines: list[str] = field(default_factory=list)
    running: bool = True
    closed: bool = False

    def push(self, line: str) -> None:
        self.lines.append(line)

    def end(self) -> None:
        self.running = False

    def read_lines(self) -> list[str]:
        drained, self.lines = self.lines, []
        return drained

    def alive(self) -> bool:
        return self.running and not self.closed

    def close(self) -> None:
        self.closed = True


ExecHandler = Callable[[str, list[str]], CommandResult]
# 컨테이너 실행을 흉내 낸다: (이름, run 옵션, command)로 결과 파일·inspect·종료 코드를 준비
RunHandler = Callable[[str, list[str], list[str] | None], None]


class FakeDocker:
    """메모리 Docker. 컨테이너 inspect 값을 직접 바꿔 identity 변경을 흉내 낼 수 있다."""

    def __init__(
        self, exec_handler: ExecHandler | None = None, run_handler: RunHandler | None = None
    ) -> None:
        self.containers: dict[str, dict[str, Any]] = {}
        self.images: dict[str, str] = {}
        self.streams: dict[str, FakeLogStream] = {}
        self.networks: set[str] = set()
        self.log_history: dict[str, list[str]] = {}
        self.exit_codes: dict[str, int | None] = {}  # wait 결과. None이면 제한 시간 초과
        self.exec_handler = exec_handler
        self.run_handler = run_handler
        self.calls: list[tuple[str, Any]] = []
        self._counter = 0

    def inspect(self, name: str) -> dict[str, Any] | None:
        self.calls.append(("inspect", name))
        data = self.containers.get(name)
        return copy.deepcopy(data) if data is not None else None

    def image_id(self, ref: str) -> str | None:
        return self.images.get(ref)

    def logs_follow(self, name: str, since: str | None = None) -> LogStream:
        self.calls.append(("logs_follow", name, since))
        return self.streams.setdefault(name, FakeLogStream())

    def logs_once(self, name: str, timestamps: bool = False) -> list[str]:
        """log_history를 그대로 돌려준다. timestamps 시험에는 시각이 붙은 줄을 넣어 둔다."""
        self.calls.append(("logs_once", name, timestamps))
        if name not in self.containers:
            raise DockerError(f"docker logs 실패: No such container: {name}")
        return list(self.log_history.get(name, []))

    def run(self, options: list[str], image: str, command: list[str] | None = None) -> str:
        self._counter += 1
        name = options[options.index("--name") + 1]
        container_id = f"{self._counter:064x}"
        self.containers[name] = {
            "Id": container_id,
            "Name": f"/{name}",
            "Image": self.images.get(image, image),
            "State": {"Running": True},
            "Config": {"Cmd": command},
        }
        self.calls.append(("run", options, image, command))
        if self.run_handler is not None:
            self.run_handler(name, options, command)
        return container_id

    def wait(self, name: str, timeout: float) -> int | None:
        self.calls.append(("wait", name, timeout))
        if name not in self.containers:
            raise DockerError(f"docker wait 실패: No such container: {name}", 1)
        return self.exit_codes.get(name, 0)

    def logs_capped(self, name: str, max_bytes: int) -> tuple[bytes, bool]:
        self.calls.append(("logs_capped", name, max_bytes))
        data = "\n".join(self.log_history.get(name, [])).encode("utf-8")
        return data[:max_bytes], len(data) > max_bytes

    def exec(self, name: str, command: list[str], timeout: float = 30.0) -> CommandResult:
        self.calls.append(("exec", name, command))
        if self.exec_handler is None:
            return CommandResult(0, "", "")
        return self.exec_handler(name, command)

    def stop(self, name: str) -> CommandResult:
        self.calls.append(("stop", name))
        self.containers.pop(name, None)
        if name in self.streams:
            self.streams[name].end()
        return CommandResult(0, "", "")

    def build(
        self, context: Path, dockerfile: Path, tag: str, build_args: dict[str, str] | None = None
    ) -> str:
        self.calls.append(("build", str(dockerfile), tag, build_args))
        image = "sha256:" + f"{len(self.images) + 1:064x}"
        self.images[tag] = image
        return image

    def network_create(self, name: str, labels: dict[str, str]) -> CommandResult:
        self.calls.append(("network_create", name, labels))
        self.networks.add(name)
        return CommandResult(0, name, "")

    def network_remove(self, name: str) -> CommandResult:
        self.calls.append(("network_remove", name))
        self.networks.discard(name)
        return CommandResult(0, name, "")
