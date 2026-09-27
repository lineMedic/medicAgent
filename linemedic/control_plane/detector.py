"""로그 감지 (W07, spec 15 §3.1, docs/05 ①, D72).

MES stdout JSON Lines → 정제·보관 → 오류 signature → problem_fingerprint → 60초 3회
→ 사건 생성 또는 병합.

- fingerprint = SHA-256(canonical JSON `[version, service, error_type, top_frame, endpoint]`).
  error_type에는 오류를 구별하는 `error_field`를 붙이고,
  request_id·lot_id·timestamp·줄 번호는 넣지 않는다.
  run_id도 넣지 않는다(격리는 routing_scope).
- service는 로그 줄의 값이 아니라 로그 source가 속한 등록 서비스로 정한다(로그는 비신뢰 데이터).
- 같은 run·fingerprint의 활성 사건이 있으면 count·last_seen·evidence만 늘린다. 가장 최근 사건이
  terminal이면 그 사건에 붙이고 새 사건·새 generation을 만들지 않는다(spec 04 §8).
- 60초 창은 관찰 시각 기준이다. 실시간 관찰은 monotonic 시계, 쌓인 로그를 읽는 detect-once는
  Docker daemon이 붙인 수신 시각을 쓴다. 로그 내용 안의 시각은 조작될 수 있어 쓰지 않는다.
- fingerprint가 같다고 원인이 같은 것은 아니다(중복 후보 키일 뿐).
"""

import re
from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from linemedic.common.canonical_json import (
    StrictJSONError,
    canonical_dumps,
    loads_strict,
    sha256_hex,
)
from linemedic.common.clock import Clock, to_rfc3339
from linemedic.common.config import LineMedicConfig
from linemedic.common.ids import new_id
from linemedic.control_plane import audit, checkpoints, evidence
from linemedic.control_plane.log_store import LogRecord, LogStore
from linemedic.control_plane.redaction import clean_text
from linemedic.control_plane.store import Store, Tx
from linemedic.integrations.docker import DockerPort

FINGERPRINT_VERSION = "fp-v1"
DETECTOR_ACTOR = "detector"
ACTIVE_STATUSES = (
    "NEW",
    "INVESTIGATING",
    "VALIDATING",
    "PR_OPENED",
    "DEPLOYING",
    "VERIFYING",
    "EXECUTION_UNKNOWN",
)
MAX_LINE_BYTES = 65536
MAX_LOG_CHARS = 8192
MAX_KEY_CHARS = 200
_LINE_SUFFIX = re.compile(r":\d+$")
_DOCKER_TIMESTAMP = re.compile(
    r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?Z (.*)$", re.DOTALL
)
DETECT_ONCE_INTEGRATION = "detector.docker_logs"


@dataclass(frozen=True)
class Signature:
    service: str
    error_type: str
    top_frame: str
    endpoint: str


def parse_line(line: str) -> dict[str, Any] | None:
    """JSON 객체 한 줄만 받는다. JSON이 아니거나 중복 key·NaN·객체가 아니면 None."""
    try:
        data = loads_strict(line.encode("utf-8"), max_bytes=MAX_LINE_BYTES)
    except StrictJSONError:
        return None
    return data if isinstance(data, dict) else None


def parse_docker_timestamped(line: str) -> tuple[int, datetime, str] | None:
    """`docker logs --timestamps` 줄 → (epoch 나노초, UTC 시각, 원래 줄). 형식이 아니면 None.

    시각은 Docker daemon이 줄을 받은 시각이라 로그 내용(비신뢰)으로 바꿀 수 없다.
    """
    match = _DOCKER_TIMESTAMP.match(line)
    if match is None:
        return None
    base = datetime.strptime(match.group(1), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=UTC)
    nanos = int((match.group(2) or "").ljust(9, "0"))
    epoch_ns = int(base.timestamp()) * 1_000_000_000 + nanos
    return epoch_ns, base + timedelta(microseconds=nanos // 1000), match.group(3)


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value[:MAX_KEY_CHARS] if value else None


def normalize_top_frame(value: str) -> str:
    """`module:function`만 남긴다. 끝에 붙은 줄 번호(`:123`)는 뗀다."""
    return _LINE_SUFFIX.sub("", value)


def normalize_endpoint(value: str) -> str:
    """쿼리·fragment를 뗀 path. 끝의 `/`는 root가 아니면 뗀다."""
    path = value.split("?", 1)[0].split("#", 1)[0]
    return path.rstrip("/") or "/"


def signature(event: Mapping[str, Any], service: str) -> Signature | None:
    """오류 이벤트(error_type·top_frame·path가 모두 있는 줄)만 signature를 만든다."""
    error_type = _text(event.get("error_type"))
    top_frame = _text(event.get("top_frame"))
    path = _text(event.get("path"))
    if error_type is None or top_frame is None or path is None:
        return None
    error_field = _text(event.get("error_field"))
    if error_field is not None:
        error_type = f"{error_type}:{error_field}"
    return Signature(
        service=service,
        error_type=error_type,
        top_frame=normalize_top_frame(top_frame),
        endpoint=normalize_endpoint(path),
    )


def problem_fingerprint(sig: Signature, version: str = FINGERPRINT_VERSION) -> str:
    return sha256_hex([version, sig.service, sig.error_type, sig.top_frame, sig.endpoint])


@dataclass(frozen=True)
class DetectorSettings:
    run_id: str
    routing_scope: str
    service: str
    line_id: str
    repository_id: int
    window_seconds: int = 60
    min_occurrences: int = 3
    fingerprint_version: str = FINGERPRINT_VERSION


def settings_for_run(
    config: LineMedicConfig, run_id: str, routing_scope: str, service: str
) -> DetectorSettings:
    """등록 서비스 catalog(config)와 run manifest의 routing scope로 감지 설정을 만든다.

    repository ID가 아직 없으면(G2 전) 0으로 둔다. Issue 연결(W24)에는 실제 ID가 필요하다.
    """
    if service not in config.services:
        raise ValueError(f"등록되지 않은 서비스: {service!r}")
    return DetectorSettings(
        run_id=run_id,
        routing_scope=routing_scope,
        service=service,
        line_id=config.services[service].line_id,
        repository_id=config.repository.id or 0,
        window_seconds=config.detector.dedupe_window_seconds,
        min_occurrences=config.detector.dedupe_min_occurrences,
    )


@dataclass(frozen=True)
class Occurrence:
    observed_at: str
    source: str
    event: dict[str, Any]


@dataclass(frozen=True)
class LineOutcome:
    error: bool  # 오류 signature가 있는 줄인가
    action: str | None = None  # "created" | "updated" | None(창에 쌓기만 함)
    incident_id: str | None = None


@dataclass
class ObservationSummary:
    lines: int = 0
    errors: int = 0
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)

    def record(self, outcome: LineOutcome) -> None:
        self.lines += 1
        self.errors += outcome.error
        if outcome.incident_id is None:
            return
        target = self.created if outcome.action == "created" else self.updated
        if outcome.incident_id not in target:
            target.append(outcome.incident_id)


class Detector:
    """한 run·서비스의 로그를 관찰한다. 60초 창은 메모리에 두고 사건은 DB에 기록한다."""

    def __init__(
        self,
        store: Store,
        settings: DetectorSettings,
        clock: Clock,
        log_store: LogStore,
        eval_terms: Iterable[str] = (),
    ) -> None:
        self.store = store
        self.settings = settings
        self.clock = clock
        self.log_store = log_store
        self.eval_terms = tuple(eval_terms)
        self._pending: dict[str, deque[tuple[float, Occurrence]]] = {}

    def observe_line(
        self, line: str, source_identity: str, at: datetime | None = None
    ) -> LineOutcome:
        """한 줄을 관찰한다. 오류 줄이면 창에 쌓고, 사건을 만들거나 늘렸으면 그 ID를 돌려준다.

        `at`(Docker daemon 수신 시각)을 주면 그 시각으로 기록하고 60초 창도 그 시각으로 센다.
        한 Detector에서는 한 가지 시각 기준만 쓴다.
        """
        settings = self.settings
        if at is None:
            now = to_rfc3339(self.clock.utc_now())
            monotonic = self.clock.monotonic()
        else:
            now = to_rfc3339(at)
            monotonic = at.timestamp()
        self.log_store.append(
            settings.run_id,
            settings.service,
            LogRecord(now, source_identity, clean_text(line, self.eval_terms, MAX_LOG_CHARS)),
        )
        event = parse_line(line)
        sig = signature(event, settings.service) if event is not None else None
        if sig is None:
            return LineOutcome(error=False)
        fingerprint = problem_fingerprint(sig, settings.fingerprint_version)
        occurrence = Occurrence(now, source_identity, event)
        with self.store.tx() as tx:
            incident = self._latest_incident(tx, fingerprint)
            if incident is not None:
                self._merge(tx, incident, [occurrence])
                return LineOutcome(True, "updated", incident["id"])
            window = self._pending.setdefault(fingerprint, deque())
            window.append((monotonic, occurrence))
            while window and monotonic - window[0][0] > settings.window_seconds:
                window.popleft()
            if len(window) < settings.min_occurrences:
                return LineOutcome(error=True)
            occurrences = [item for _, item in window]
            incident_id = self._create(tx, sig, fingerprint, occurrences)
            window.clear()
            return LineOutcome(True, "created", incident_id)

    def observe_lines(self, lines: Iterable[str], source_identity: str) -> ObservationSummary:
        summary = ObservationSummary()
        for line in lines:
            if line.strip():
                summary.record(self.observe_line(line, source_identity))
        return summary

    def _latest_incident(self, tx: Tx, fingerprint: str) -> Any:
        """활성 사건이 있으면 그것, 없으면 가장 최근 사건(terminal 포함)."""
        marks = ", ".join("?" for _ in ACTIVE_STATUSES)
        return tx.one(
            "SELECT * FROM incidents"
            " WHERE run_id = ? AND fingerprint = ? AND fingerprint_version = ?"
            f" ORDER BY status IN ({marks}) DESC, last_seen DESC, rowid DESC LIMIT 1",
            (
                self.settings.run_id,
                fingerprint,
                self.settings.fingerprint_version,
                *ACTIVE_STATUSES,
            ),
        )

    def _add_evidence(self, tx: Tx, incident_id: str, occurrences: list[Occurrence]) -> None:
        run_id = self.settings.run_id
        room = evidence.MAX_LOG_EVIDENCE_PER_INCIDENT - evidence.count_kind(
            tx, run_id, incident_id, "log_error"
        )
        for occurrence in occurrences[: max(0, room)]:
            evidence.add_evidence(
                tx,
                run_id=run_id,
                incident_id=incident_id,
                kind="log_error",
                observed_at=occurrence.observed_at,
                source_identity=occurrence.source,
                payload={"event": occurrence.event},
                terms=self.eval_terms,
            )

    def _merge(self, tx: Tx, incident: Any, occurrences: list[Occurrence]) -> None:
        """상태 전이가 아니므로 version은 그대로 두고 count·last_seen·evidence만 늘린다."""
        tx.execute(
            "UPDATE incidents SET count = count + ?, last_seen = MAX(last_seen, ?) WHERE id = ?",
            (len(occurrences), occurrences[-1].observed_at, incident["id"]),
        )
        self._add_evidence(tx, incident["id"], occurrences)

    def _create(
        self, tx: Tx, sig: Signature, fingerprint: str, occurrences: list[Occurrence]
    ) -> str:
        settings = self.settings
        incident_id = new_id("INC")
        details = {
            "signature": {
                "service": sig.service,
                "error_type": clean_text(sig.error_type, self.eval_terms, MAX_KEY_CHARS),
                "top_frame": clean_text(sig.top_frame, self.eval_terms, MAX_KEY_CHARS),
                "endpoint": clean_text(sig.endpoint, self.eval_terms, MAX_KEY_CHARS),
            }
        }
        tx.execute(
            "INSERT INTO incidents(id, run_id, routing_scope, repository_id, fingerprint,"
            " fingerprint_version, source_kind, status, service, line_id, count, first_seen,"
            " last_seen, details_json) VALUES (?, ?, ?, ?, ?, ?, 'LOG', 'NEW', ?, ?, ?, ?, ?, ?)",
            (
                incident_id,
                settings.run_id,
                settings.routing_scope,
                settings.repository_id,
                fingerprint,
                settings.fingerprint_version,
                settings.service,
                settings.line_id,
                len(occurrences),
                occurrences[0].observed_at,
                occurrences[-1].observed_at,
                canonical_dumps(details),
            ),
        )
        self._add_evidence(tx, incident_id, occurrences)
        audit.append(
            tx,
            settings.run_id,
            incident_id,
            DETECTOR_ACTOR,
            "INCIDENT_DETECTED",
            {
                "fingerprint": fingerprint,
                "fingerprint_version": settings.fingerprint_version,
                "count": len(occurrences),
                "window_seconds": settings.window_seconds,
                "min_occurrences": settings.min_occurrences,
            },
        )
        return incident_id


def run_detect_once(
    store: Store, detector: Detector, docker: DockerPort, container: str
) -> dict[str, Any]:
    """컨테이너 로그를 한 번 읽어 감지기에 넣는다(`make detect-once`).

    - `docker logs --timestamps`의 daemon 수신 시각으로 60초 창을 센다
    - 처리한 마지막 시각을 `integration_state`에 남겨, 다시 실행해도 같은 줄을 다시 세지 않는다
    - 60초 창은 실행 사이에 이어지지 않는다(상시 감시는 W13)
    """
    key = f"{detector.settings.run_id}:{container}"
    with store.read() as tx:
        state = checkpoints.get(tx, DETECT_ONCE_INTEGRATION, key)
    last_ns = int(state["last_ns"]) if state else -1
    summary = ObservationSummary()
    skipped = unparsed = 0
    for raw in docker.logs_once(container, timestamps=True):
        parsed = parse_docker_timestamped(raw)
        if parsed is None:
            unparsed += 1
            continue
        epoch_ns, at, text = parsed
        if epoch_ns <= last_ns:
            skipped += 1
            continue
        if text.strip():
            summary.record(detector.observe_line(text, f"container:{container}", at=at))
        with store.tx() as tx:
            checkpoints.put(tx, DETECT_ONCE_INTEGRATION, key, {"last_ns": epoch_ns})
        last_ns = epoch_ns
    return {
        "lines": summary.lines,
        "errors": summary.errors,
        "created": summary.created,
        "updated": summary.updated,
        "skipped": skipped,
        "unparsed": unparsed,
    }
