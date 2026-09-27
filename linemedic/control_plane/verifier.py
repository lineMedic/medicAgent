"""독립 업무 verifier (W05, spec 08 §4~§9).

원래 사용자 경로 `GET /defects/summary?lot_id=`로 contract의 모든 case를 t=0·10·20·30초에 호출하고,
t=60초까지 로그 관찰·identity·fixture를 확인한 뒤에만 PASS한다. 실제 반증은 FAIL로 조기 종료한다.
앱의 자기보고(`status=resolved` 등)를 믿지 않는다.

`persist_result`(2부)는 결과를 `verifications`에 저장하고 verifier 주체로만 incident를 전이한다.
RESOLVED를 쓰는 제품 경로는 이것 하나다(INV-01).
판정 엔진이 내는 결과의 `resolved_written`은 false이고, PASS를 RESOLVED로 기록했을 때만
저장본에서 true가 된다.
"""

import base64
import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Annotated, Any, Literal, Protocol
from urllib.parse import urlencode

from pydantic import BaseModel, ConfigDict, Field, model_validator

from linemedic.common.canonical_json import StrictJSONError, canonical_dumps, loads_strict
from linemedic.common.clock import Clock, to_rfc3339
from linemedic.common.config import read_toml, validate_model
from linemedic.common.ids import new_id
from linemedic.control_plane import audit
from linemedic.control_plane.notifications import outbox
from linemedic.control_plane.observer import ObserverStatus
from linemedic.control_plane.state import Actor, coupled_transition, transition_incident
from linemedic.control_plane.store import Tx
from linemedic.integrations.docker import DockerPort

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACTS_DIR = REPO_ROOT / "linemedic" / "contracts"
EVAL_DIR = REPO_ROOT / "linemedic" / "eval"
DEFAULT_CONTRACT = CONTRACTS_DIR / "defect-summary-v1.toml"
SUMMARY_PATH = "/defects/summary"
RESPONSE_KEYS = frozenset({"lot_id", "total_defects", "by_inspector"})
POLL_SECONDS = 1.0

# ── 계약 ───────────────────────────────────────────────────────

Assertion = Literal[
    "strict_response_schema",
    "exact_lot_id",
    "exact_total_defects",
    "exact_by_inspector_mapping",
    "sum_groups_equals_total",
]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class ExpectedBody(_Model):
    lot_id: str
    total_defects: Annotated[int, Field(ge=0)]
    by_inspector: dict[str, Annotated[int, Field(ge=0)]]


class CaseExpect(_Model):
    status: int
    body: ExpectedBody


class CaseRequest(_Model):
    method: Literal["GET"]
    path: str
    query: dict[str, str]


class ContractCase(_Model):
    id: str
    request: CaseRequest | None = None
    expect: CaseExpect | None = None
    fixture_ref: str | None = None

    @model_validator(mode="after")
    def _one_source(self) -> "ContractCase":
        inline = self.request is not None and self.expect is not None
        referenced = self.fixture_ref is not None
        if inline == referenced or (referenced and (self.request or self.expect)):
            raise ValueError("case는 request+expect 또는 fixture_ref 중 하나만 가진다")
        return self


class Observation(_Model):
    # spec 08 §5 core 하한: 표본 4회(t=0·10·20·30), 관찰 60초. 계약으로 더 약하게 만들 수 없다.
    samples: Annotated[int, Field(ge=4)]
    interval_seconds: Annotated[int, Field(ge=0)]
    recurrence_window_seconds: Annotated[int, Field(ge=60)]
    # core 판정 규칙(spec 08 §5)이라 계약으로 끌 수 없다. false를 쓰면 계약 로드가 실패한다.
    require_all_samples: Literal[True]
    require_log_observer_healthy: Literal[True]
    require_runtime_identity_unchanged: Literal[True]

    @model_validator(mode="after")
    def _samples_inside_window(self) -> "Observation":
        if (self.samples - 1) * self.interval_seconds >= self.recurrence_window_seconds:
            raise ValueError("마지막 표본은 관찰 구간이 끝나기 전이어야 한다")
        return self


class Contract(_Model):
    contract_id: str
    entry_service: str
    assertions: list[Assertion]
    cases: Annotated[list[ContractCase], Field(min_length=1)]
    observation: Observation


def load_contract(path: Path = DEFAULT_CONTRACT) -> tuple[Contract, str]:
    """계약을 엄격하게 읽고 파일 바이트의 SHA-256(contract_sha256)을 함께 돌려준다."""
    contract = validate_model(Contract, read_toml(path), "contract")
    return contract, hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass(frozen=True)
class ResolvedCase:
    case_id: str
    path: str
    query: dict[str, str]
    expect_status: int
    expect_body: dict[str, Any]


def resolve_cases(contract: Contract, eval_dir: Path = EVAL_DIR) -> list[ResolvedCase]:
    """inline case와 fixture_ref case(holdout 기대값은 eval에서 읽음)를 같은 형태로 만든다."""
    resolved = []
    for case in contract.cases:
        if case.fixture_ref is not None:
            fixture = json.loads(
                (eval_dir / f"{case.fixture_ref}.json").read_text(encoding="utf-8")
            )
            expected = validate_model(ExpectedBody, fixture["expected"], case.fixture_ref)
            if expected.lot_id != fixture["input"]["lot_id"]:
                raise ValueError(f"{case.fixture_ref}: 입력과 기대값의 lot_id가 다르다")
            resolved.append(
                ResolvedCase(
                    case.id, SUMMARY_PATH, {"lot_id": expected.lot_id}, 200, expected.model_dump()
                )
            )
        else:
            assert case.request is not None and case.expect is not None
            resolved.append(
                ResolvedCase(
                    case.id,
                    case.request.path,
                    dict(case.request.query),
                    case.expect.status,
                    case.expect.body.model_dump(),
                )
            )
    return resolved


# ── HTTP ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: bytes


class RequestTimeout(Exception):
    """표본 요청이 제한 시간 안에 응답하지 않음."""


class RequestFailed(Exception):
    """표본 요청이 응답 없이 실패함(연결 실패 등)."""


class HttpClient(Protocol):
    def get(self, path: str, query: Mapping[str, str]) -> HttpResponse: ...


PROBE_CODE = (
    "import base64, json, sys, urllib.error, urllib.request\n"
    "def out(payload):\n"
    "    print(json.dumps(payload))\n"
    "try:\n"
    "    with urllib.request.urlopen(sys.argv[1], timeout=float(sys.argv[2])) as r:\n"
    "        out({'status': r.status, 'body': base64.b64encode(r.read()).decode()})\n"
    "except urllib.error.HTTPError as e:\n"
    "    out({'status': e.code, 'body': base64.b64encode(e.read()).decode()})\n"
    "except urllib.error.URLError as e:\n"
    "    kind = 'timeout' if isinstance(e.reason, TimeoutError) else type(e.reason).__name__\n"
    "    out({'error': kind})\n"
    "except TimeoutError:\n"
    "    out({'error': 'timeout'})\n"
    "except Exception as e:\n"
    "    out({'error': type(e).__name__})\n"
)


class ProberHttp:
    """같은 내부 network의 신뢰 prober 컨테이너에서 대상 MES로 HTTP 요청을 보낸다.

    prober는 신뢰 base image와 stdlib 코드만 쓰고, 응답 판정은 host의 verifier가 한다.
    MES는 internal network에 있어 host port를 열지 않는다.
    """

    def __init__(
        self, docker: DockerPort, prober: str, base_url: str, timeout: float = 5.0
    ) -> None:
        self.docker = docker
        self.prober = prober
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def get(self, path: str, query: Mapping[str, str]) -> HttpResponse:
        url = self.base_url + path + (f"?{urlencode(dict(query))}" if query else "")
        result = self.docker.exec(
            self.prober,
            ["python", "-c", PROBE_CODE, url, str(self.timeout)],
            timeout=self.timeout + 15,
        )
        if result.returncode == 124:
            raise RequestTimeout("prober 실행 timeout")
        try:
            payload = json.loads(result.stdout.strip().splitlines()[-1])
        except (IndexError, json.JSONDecodeError):
            raise RequestFailed(f"prober 응답을 읽을 수 없음 (exit {result.returncode})") from None
        if "error" in payload:
            if payload["error"] == "timeout":
                raise RequestTimeout("요청 timeout")
            raise RequestFailed(str(payload["error"]))
        return HttpResponse(int(payload["status"]), base64.b64decode(payload["body"]))


# ── 판정 ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class FailedAssertion:
    case_id: str
    assertion: str
    field: str
    expected: Any
    actual: Any


def _is_count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def evaluate_case(case: ResolvedCase, response: HttpResponse) -> list[FailedAssertion]:
    """한 case 응답을 assertion 5종과 상태 코드로 검사한다. 모든 위반을 모아 돌려준다."""
    failures: list[FailedAssertion] = []

    def fail(assertion: str, field_name: str, expected: Any, actual: Any) -> None:
        failures.append(FailedAssertion(case.case_id, assertion, field_name, expected, actual))

    if response.status != case.expect_status:
        fail("status", "status", case.expect_status, response.status)
        return failures
    try:
        body = loads_strict(response.body)
    except StrictJSONError as exc:
        fail("strict_response_schema", "body", "JSON object", f"invalid JSON: {exc}")
        return failures
    if not isinstance(body, dict):
        fail("strict_response_schema", "body", "JSON object", type(body).__name__)
        return failures
    if set(body) != RESPONSE_KEYS:
        fail("strict_response_schema", "keys", sorted(RESPONSE_KEYS), sorted(body))
    expected = case.expect_body
    lot_id = body.get("lot_id")
    if not isinstance(lot_id, str) or lot_id != expected["lot_id"]:
        fail("exact_lot_id", "lot_id", expected["lot_id"], lot_id)
    total = body.get("total_defects")
    if not _is_count(total) or total != expected["total_defects"]:
        fail("exact_total_defects", "total_defects", expected["total_defects"], total)
    groups = body.get("by_inspector")
    groups_valid = isinstance(groups, dict) and all(
        isinstance(key, str) and _is_count(value) for key, value in groups.items()
    )
    if not groups_valid or groups != expected["by_inspector"]:
        fail("exact_by_inspector_mapping", "by_inspector", expected["by_inspector"], groups)
    if groups_valid and _is_count(total) and sum(groups.values()) != total:
        fail("sum_groups_equals_total", "by_inspector", total, sum(groups.values()))
    return failures


class FixtureGuard:
    """관찰 구간 동안 fixture·계약 파일이 바뀌지 않았는지 확인한다."""

    def __init__(self, paths: Sequence[Path]) -> None:
        self.paths = sorted(paths, key=lambda p: p.name)
        self.digest = self._compute()

    def _compute(self) -> str:
        digest = hashlib.sha256()
        for path in self.paths:
            digest.update(path.name.encode("utf-8") + b"\0")
            digest.update(path.read_bytes() if path.exists() else b"<missing>")
            digest.update(b"\0")
        return digest.hexdigest()

    def changed(self) -> bool:
        return self._compute() != self.digest


class Observer(Protocol):
    def poll(self) -> ObserverStatus: ...


@dataclass
class VerificationResult:
    verification_id: str
    origin: str
    contract_id: str
    contract_sha256: str
    verdict: str
    reason: str
    samples_completed: int
    samples_required: int
    observation_complete: bool
    failed_assertions: list[dict[str, Any]]
    resolved_written: bool
    started_at: str
    ended_at: str
    elapsed_seconds: float
    target: dict[str, Any] = field(default_factory=dict)
    observer: dict[str, Any] | None = None
    fixture_sha256: str | None = None
    detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class VerificationRun:
    """한 번의 검증. `step()`을 반복 호출하면 판정이 나올 때까지 None을 돌려준다.

    매 step마다 환경을 먼저 보고, 예정된 표본을 호출한다(D67). reason 코드:
    - INCONCLUSIVE `identity_changed`: container·image ID 변경 또는 대상 사라짐
    - FAIL `error_recurred`: 같은 오류 signature 재발
    - INCONCLUSIVE `observer_gap`: 로그 스트림 끊김(재발 없음으로 합격시키지 않음)
    - INCONCLUSIVE `fixture_changed`: 계약·fixture 파일 변경
    - INCONCLUSIVE `sample_unanswered`: 표본 요청 timeout·연결 실패
    - FAIL `business_error`: 기대와 다른 HTTP 상태 코드(한 case라도)
    - FAIL `content_mismatch`: 상태 코드는 맞지만 본문이 계약과 다름
    - INCONCLUSIVE `verifier_error`: 판정 중 예상하지 못한 오류(`verify`가 잡아 기록한다)
    - PASS `all_checks_passed`: 모든 표본 통과 후 t=recurrence_window_seconds(60초) 도달
    """

    def __init__(
        self,
        *,
        contract: Contract,
        contract_sha256: str,
        cases: Sequence[ResolvedCase],
        http: HttpClient,
        observer: Observer,
        clock: Clock,
        origin: str,
        target: dict[str, Any],
        fixture_guard: FixtureGuard | None = None,
        verification_id: str | None = None,
        poll_seconds: float = POLL_SECONDS,
    ) -> None:
        observation = contract.observation
        self.contract = contract
        self.contract_sha256 = contract_sha256
        self.cases = list(cases)
        self.http = http
        self.observer = observer
        self.clock = clock
        self.origin = origin
        self.target = dict(target)
        self.fixture_guard = fixture_guard
        self.verification_id = verification_id or new_id("VER")
        self.poll_seconds = poll_seconds
        self.offsets = [i * observation.interval_seconds for i in range(observation.samples)]
        self.end_seconds = float(observation.recurrence_window_seconds)
        self.t0 = clock.monotonic()
        self.started_at = to_rfc3339(clock.utc_now())
        self.next_sample = 0
        self.samples_completed = 0
        self.failed: list[FailedAssertion] = []
        self.last_status: ObserverStatus | None = None
        self.result: VerificationResult | None = None

    def elapsed(self) -> float:
        return self.clock.monotonic() - self.t0

    def step(self) -> VerificationResult | None:
        if self.result is not None:
            return self.result
        verdict = self._check_environment()
        if verdict is not None:
            return verdict
        while (
            self.next_sample < len(self.offsets)
            and self.elapsed() >= self.offsets[self.next_sample]
        ):
            verdict = self._take_sample()
            if verdict is not None:
                return verdict
        if self.next_sample == len(self.offsets) and self.elapsed() >= self.end_seconds:
            # 표본 응답이 느려 이 step 안에서 시간이 흘렀을 수 있다.
            # PASS 직전에 관찰 상태를 다시 본다.
            verdict = self._check_environment()
            if verdict is not None:
                return verdict
            return self._finish("PASS", "all_checks_passed")
        return None

    def seconds_until_next_event(self) -> float:
        elapsed = self.elapsed()
        candidates = [self.poll_seconds, self.end_seconds - elapsed]
        if self.next_sample < len(self.offsets):
            candidates.append(self.offsets[self.next_sample] - elapsed)
        return max(0.0, min(candidates))

    def _check_environment(self) -> VerificationResult | None:
        status = self.observer.poll()
        self.last_status = status
        if status.identity_changed:
            return self._finish("INCONCLUSIVE", "identity_changed", status.detail)
        if status.recurrences > 0:
            return self._finish("FAIL", "error_recurred", f"같은 오류 재발 {status.recurrences}회")
        if status.stream_gap:
            return self._finish("INCONCLUSIVE", "observer_gap", status.detail)
        if self.fixture_guard is not None and self.fixture_guard.changed():
            return self._finish("INCONCLUSIVE", "fixture_changed", "fixture·계약 파일이 바뀜")
        return None

    def _take_sample(self) -> VerificationResult | None:
        """표본 한 번에 모든 case를 호출한다.

        관찰한 반증(틀린 응답)은 다른 case의 무응답보다 우선한다.
        """
        failures: list[FailedAssertion] = []
        unanswered: list[str] = []
        for case in self.cases:
            try:
                response = self.http.get(case.path, case.query)
            except (RequestTimeout, RequestFailed) as exc:
                unanswered.append(f"{case.case_id}: {exc}")
                continue
            failures.extend(evaluate_case(case, response))
        if not unanswered:
            self.next_sample += 1
            self.samples_completed += 1
        detail = "; ".join(unanswered) or None
        if failures:
            self.failed = failures
            status_failed = any(failure.assertion == "status" for failure in failures)
            reason = "business_error" if status_failed else "content_mismatch"
            return self._finish("FAIL", reason, f"응답 없는 case: {detail}" if detail else None)
        if unanswered:
            return self._finish("INCONCLUSIVE", "sample_unanswered", detail)
        return None

    def abort(self, detail: str) -> VerificationResult:
        """판정 중 예상하지 못한 오류. 관찰을 이어 PASS로 추정하지 않고 INCONCLUSIVE로 끝낸다."""
        if self.result is not None:
            return self.result
        return self._finish("INCONCLUSIVE", "verifier_error", detail)

    def _finish(self, verdict: str, reason: str, detail: str | None = None) -> VerificationResult:
        status = self.last_status
        self.result = VerificationResult(
            verification_id=self.verification_id,
            origin=self.origin,
            contract_id=self.contract.contract_id,
            contract_sha256=self.contract_sha256,
            verdict=verdict,
            reason=reason,
            samples_completed=self.samples_completed,
            samples_required=len(self.offsets),
            observation_complete=verdict == "PASS",
            failed_assertions=[asdict(failure) for failure in self.failed],
            resolved_written=False,
            started_at=self.started_at,
            ended_at=to_rfc3339(self.clock.utc_now()),
            elapsed_seconds=round(self.elapsed(), 3),
            target=self.target,
            observer=asdict(status) if status is not None else None,
            fixture_sha256=self.fixture_guard.digest if self.fixture_guard is not None else None,
            detail=detail,
        )
        return self.result


def verify(**kwargs: Any) -> VerificationResult:
    """`VerificationRun`을 만들어 판정이 나올 때까지 진행한다(`drive`)."""
    return drive(VerificationRun(**kwargs))


def drive(run: VerificationRun) -> VerificationResult:
    """판정이 나올 때까지 step을 반복한다. 시간은 주입한 clock으로 흐른다.

    판정 중 예상하지 못한 예외는 INCONCLUSIVE/`verifier_error`(detail: 예외 종류)로 기록한다.
    KeyboardInterrupt 같은 중단은 호출자가 `run.abort()`로 기록한 뒤 다시 올린다.
    """
    while True:
        try:
            result = run.step()
        except Exception as exc:  # 어떤 오류도 PASS로 이어지지 않게 INCONCLUSIVE로 끝낸다
            return run.abort(type(exc).__name__)
        if result is not None:
            return result
        run.clock.sleep(max(run.seconds_until_next_event(), 0.01))


# ── 저장과 전이 (2부) ─────────────────────────────────────────

VERIFICATION_ORIGINS = frozenset({"agent_release", "human_injected_negative", "manual_integration"})
AGENT_PERFORMANCE_ORIGIN = "agent_release"
FINAL_VERDICTS = frozenset({"PASS", "FAIL", "INCONCLUSIVE"})
# FAIL·INCONCLUSIVE로 ESCALATED할 때의 reason 코드(docs/03 §5 blocker_code)
ESCALATION_REASONS = {"FAIL": "VERIFICATION_FAILED", "INCONCLUSIVE": "OBSERVATION_INCONCLUSIVE"}


@dataclass(frozen=True)
class PersistedVerification:
    verification_id: str
    verdict: str
    incident_status: str
    incident_version: int
    work_id: str | None
    work_status: str | None
    work_version: int | None
    notification_id: str | None
    resolved_written: bool
    result: dict[str, Any]


def persist_result(
    tx: Tx,
    result: VerificationResult,
    *,
    run_id: str,
    incident_id: str,
    expected_incident_version: int,
    route_id: str,
    execution_id: str | None = None,
) -> PersistedVerification:
    """판정 결과를 저장하고 verifier 주체로만 incident(와 work)를 전이한다.

    호출자의 트랜잭션 안에서 쓴다.

    - PASS → incident RESOLVED, work SUCCEEDED, `RECOVERY_VERIFIED` 알림 intent
    - FAIL·INCONCLUSIVE → incident ESCALATED, work BLOCKED, `RECOVERY_NOT_VERIFIED` 알림 intent
    - work가 없는 사건(S1b 시험 사건 등)은 incident만 전이하고 알림 intent를 넣지 않는다
      (알릴 Issue가 없다)

    incident가 VERIFYING이 아니거나 version이 다르면 예외가 나고,
    호출자의 트랜잭션이 저장까지 모두 되돌린다.
    """
    if result.origin not in VERIFICATION_ORIGINS:
        raise ValueError(f"알 수 없는 verification origin: {result.origin!r}")
    if result.verdict not in FINAL_VERDICTS:
        raise ValueError(f"최종 판정이 아니다: {result.verdict!r}")
    if result.verdict == "PASS" and not (
        result.observation_complete
        and result.samples_completed == result.samples_required
        and not result.failed_assertions
    ):
        raise ValueError("PASS는 관찰 완료·모든 표본 통과·실패 없음일 때만 저장한다")
    incident = tx.one("SELECT run_id FROM incidents WHERE id = ?", (incident_id,))
    if incident is None or incident["run_id"] != run_id:
        raise ValueError("run과 incident가 맞지 않는다")

    resolved = result.verdict == "PASS"
    stored = {**result.to_dict(), "resolved_written": resolved}
    tx.execute(
        "INSERT INTO verifications(id, run_id, incident_id, execution_id, origin, verdict,"
        " contract_id, contract_sha256, started_at, ended_at, result_json)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            result.verification_id,
            run_id,
            incident_id,
            execution_id,
            result.origin,
            result.verdict,
            result.contract_id,
            result.contract_sha256,
            result.started_at,
            result.ended_at,
            canonical_dumps(stored),
        ),
    )
    target = "RESOLVED" if resolved else "ESCALATED"
    reason = None if resolved else ESCALATION_REASONS[result.verdict]
    details = {
        "verification_id": result.verification_id,
        "verdict": result.verdict,
        "verification_reason": result.reason,
        "origin": result.origin,
    }
    work = tx.one(
        "SELECT * FROM work_items WHERE run_id = ? AND incident_id = ?", (run_id, incident_id)
    )
    work_status = work_version = notification_id = None
    if work is None:
        incident_version = transition_incident(
            tx,
            incident_id,
            expected_incident_version,
            target,
            Actor.VERIFIER,
            reason,
            details=details,
        )
    else:
        work_status = "SUCCEEDED" if resolved else "BLOCKED"
        coupled = coupled_transition(
            tx,
            incident_id=incident_id,
            expected_incident_version=expected_incident_version,
            incident_to=target,
            work_id=work["id"],
            expected_work_version=work["version"],
            work_to=work_status,
            actor=Actor.VERIFIER,
            reason=reason,
            details=details,
        )
        incident_version, work_version = coupled.incident_version, coupled.work_version
        assert coupled.outbox_event is not None
        payload = {
            "schema_version": "linemedic.v4",
            "event_type": coupled.outbox_event,
            "run_id": run_id,
            "incident_id": incident_id,
            "work_id": work["id"],
            "generation": work["generation"],
            "repository_id": work["repository_id"],
            "issue_number": work["issue_number"],
            "verification_id": result.verification_id,
            "verdict": result.verdict,
            "reason": result.reason,
            "origin": result.origin,
            "contract_id": result.contract_id,
            "contract_sha256": result.contract_sha256,
            "target": result.target,
            "samples_completed": result.samples_completed,
            "samples_required": result.samples_required,
            "observation_complete": result.observation_complete,
            "started_at": result.started_at,
            "ended_at": result.ended_at,
        }
        notification_id = outbox.enqueue(tx, work, coupled.outbox_event, payload, route_id)
    audit.append(
        tx,
        run_id,
        incident_id,
        Actor.VERIFIER,
        "VERIFICATION_RECORDED",
        {**details, "contract_id": result.contract_id, "resolved_written": resolved},
    )
    return PersistedVerification(
        verification_id=result.verification_id,
        verdict=result.verdict,
        incident_status=target,
        incident_version=incident_version,
        work_id=work["id"] if work is not None else None,
        work_status=work_status,
        work_version=work_version,
        notification_id=notification_id,
        resolved_written=resolved,
        result=stored,
    )


def agent_performance_verifications(tx: Tx, run_id: str) -> list[Any]:
    """agent 성과 집계 대상(origin=agent_release)만 읽는다. S1b·수동 통합은 뺀다(spec 09 §4)."""
    return tx.all(
        "SELECT * FROM verifications WHERE run_id = ? AND origin = ? ORDER BY started_at, id",
        (run_id, AGENT_PERFORMANCE_ORIGIN),
    )
