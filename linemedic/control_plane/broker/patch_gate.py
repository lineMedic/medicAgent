"""create_pr 제안의 패치 게이트 (W10, spec 06 §2~§4, docs/05 ⑤).

순서: 정책(diff) → 기준 base → candidate(git) → runner image → R0(base 회귀) → R1(base + 새 테스트)
→ R2(candidate). 실패하면 거기서 멈추고 검사 코드·사유를 돌려준다. git·docker를 부르므로 DB
트랜잭션 밖에서 부른다.

| 실패 | 코드 | 에이전트 수정 |
|---|---|---|
| diff 정책·적용 실패·tree 불일치 | `PATCH_PATH_DENIED`(rule) | 가능 |
| 제안 base가 run 기준과 다름 | `SOURCE_CHANGED` | 가능 |
| run 기준과 배포 관찰 base가 다름 | `SOURCE_CHANGED` | 불가 |
| run 기준·배포 base 없음, mirror·git·image 문제 | `PROTECTION_UNAVAILABLE` | 불가 |
| R0 실패, runner 오류 | `PROTECTION_UNAVAILABLE` | 불가 |
| R1이 재현을 보이지 못함 | `REPRO_NOT_FAILING`(reason) | 가능 |
| R2 실패 | `REGRESSION_FAILED`(reason) | 가능 |

검사 디렉터리는 `RUNS_DIR/<run>/checkouts/<proposal>/<n>`이다. 다시 검사하면 새 번호를 쓰고 이전
결과는 지우지 않는다(run 정리는 W19).
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from linemedic.common.clock import Clock
from linemedic.common.config import Settings
from linemedic.control_plane.broker.candidate import CandidateError, build_candidate
from linemedic.control_plane.broker.patch_policy import (
    BrokerPolicy,
    PatchDenied,
    check_diff,
    load_policy,
)
from linemedic.control_plane.broker.runner import (
    PROTECTION_UNAVAILABLE,
    Runner,
    RunnerProfile,
    StageRun,
    Verdict,
    judge_r0,
    judge_r1,
    judge_r2,
    module_of,
)
from linemedic.control_plane.codes import RUN_ID_PATTERN
from linemedic.integrations.docker import DockerPort

MIRROR_NAME = "l3-mes-api.git"
SOURCE_CHANGED = "SOURCE_CHANGED"
_RUN_ID = re.compile(RUN_ID_PATTERN)
_PROPOSAL_ID = re.compile(r"PROP-[0-9A-F]{12}")


@dataclass(frozen=True)
class GateRequest:
    run_id: str
    proposal_id: str
    base_sha: str  # 제안이 밝힌 base
    allowed_base: str | None  # run manifest의 BASELINE_COMMIT
    deploy_base: str | None  # 사건 서비스의 가장 최근 배포 관찰 base
    diff: str
    new_test_path: str
    received_at: str


@dataclass
class GateOutcome:
    passed: bool = False
    checks: list[dict[str, Any]] = field(default_factory=list)
    code: str | None = None
    reason: str | None = None
    revisable: bool = True  # False면 수정 예산과 관계없이 멈춘다(에이전트가 고칠 수 없음)
    candidate: dict[str, Any] | None = None
    workdir: Path | None = None  # candidate를 만든 검사 디렉터리(봇 PR push가 `repo.git`을 쓴다)

    def fail(
        self, check: str, code: str, reason: str, *, revisable: bool, **extra: Any
    ) -> "GateOutcome":
        self.checks.append({"check": check, "result": code, "reason": reason, **extra})
        self.code, self.reason, self.revisable = code, reason, revisable
        return self


def base_problem(request: GateRequest) -> tuple[str, str, bool] | None:
    """(코드, 사유, 수정 가능) 또는 None. 사건 때 관찰한 배포 base와 run 기준이 같아야 한다."""
    if request.allowed_base is None:
        return PROTECTION_UNAVAILABLE, "run_baseline_unconfigured", False
    if request.deploy_base is None:
        return PROTECTION_UNAVAILABLE, "deploy_base_unknown", False
    if request.deploy_base != request.allowed_base:
        return SOURCE_CHANGED, "deploy_base_differs", False
    if request.base_sha != request.allowed_base:
        return SOURCE_CHANGED, "proposal_base_mismatch", True
    return None


class PatchGate:
    def __init__(
        self, *, policy: BrokerPolicy, runner: Runner, mirror: Path, runs_dir: Path
    ) -> None:
        self.policy = policy
        self.runner = runner
        self.mirror = mirror
        self.runs_dir = runs_dir

    def check(self, request: GateRequest) -> GateOutcome:
        outcome = GateOutcome()
        rules = self.policy.rules
        try:
            plan = check_diff(request.diff, request.new_test_path, rules)
        except PatchDenied as exc:
            return outcome.fail("PATCH_POLICY", exc.code, exc.rule, revisable=True, **_path(exc))
        outcome.checks.append(
            {"check": "PATCH_POLICY", "result": "PASS", "policy_sha256": self.policy.sha256}
            | plan.record()
        )

        problem = base_problem(request)
        if problem is not None:
            code, reason, revisable = problem
            return outcome.fail("BASE", code, reason, revisable=revisable)
        outcome.checks.append({"check": "BASE", "result": "PASS", "base_sha": request.base_sha})

        workdir = self._workdir(request)
        try:
            candidate = build_candidate(
                mirror=self.mirror,
                workdir=workdir,
                base_sha=request.base_sha,
                diff=request.diff,
                plan=plan,
                rules=rules,
                proposal_id=request.proposal_id,
                committed_at=request.received_at,
            )
        except PatchDenied as exc:
            return outcome.fail("CANDIDATE", exc.code, exc.rule, revisable=True, **_path(exc))
        except CandidateError as exc:
            return outcome.fail("CANDIDATE", PROTECTION_UNAVAILABLE, exc.reason, revisable=False)
        outcome.candidate, outcome.workdir = candidate.record(), workdir
        outcome.checks.append({"check": "CANDIDATE", "result": "PASS"} | candidate.record())

        image = self.runner.image_problem()
        if image is not None:
            return outcome.fail("RUNNER", PROTECTION_UNAVAILABLE, image, revisable=False)

        regression, new_test = rules.regression_tests, request.new_test_path
        r0 = self._stage(request, workdir, "R0", candidate.trees["base"], [regression])
        if not self._record(outcome, r0, judge_r0(r0, module_of(regression))):
            return outcome
        r1 = self._stage(request, workdir, "R1", candidate.trees["repro"], [new_test])
        if not self._record(outcome, r1, judge_r1(r1, module_of(new_test))):
            return outcome
        assert r0.junit is not None  # R0 통과면 있다
        seen = [(case.classname, case.name) for case in r0.junit.cases]
        r2 = self._stage(
            request, workdir, "R2", candidate.trees["candidate"], [new_test, regression]
        )
        if not self._record(outcome, r2, judge_r2(r2, module_of(new_test), seen)):
            return outcome
        outcome.passed = True
        return outcome

    def _workdir(self, request: GateRequest) -> Path:
        if not _RUN_ID.fullmatch(request.run_id) or not _PROPOSAL_ID.fullmatch(request.proposal_id):
            raise ValueError("run·proposal ID 형식이 아니다")
        root = self.runs_dir / request.run_id / "checkouts" / request.proposal_id
        root.mkdir(parents=True, exist_ok=True)
        used = [int(p.name) for p in root.iterdir() if p.name.isdigit()]
        return root / str(max(used, default=0) + 1)

    def _stage(
        self, request: GateRequest, workdir: Path, stage: str, tree: Path, tests: list[str]
    ) -> StageRun:
        return self.runner.run_stage(
            stage=stage,
            name=f"lm-runner-{request.proposal_id.lower()}-{workdir.name}-{stage.lower()}",
            tree=tree,
            results=workdir / "results" / stage,
            logs=workdir / "logs",
            tests=tests,
            labels={
                "linemedic.role": "runner",
                "linemedic.run": request.run_id,
                "linemedic.proposal": request.proposal_id,
                "linemedic.stage": stage,
            },
        )

    @staticmethod
    def _record(outcome: GateOutcome, run: StageRun, verdict: Verdict) -> bool:
        details = {k: v for k, v in run.record().items() if k != "stage"}
        if verdict.ok:
            outcome.checks.append({"check": run.stage, "result": "PASS"} | details)
            return True
        assert verdict.code is not None and verdict.reason is not None
        # R0 실패와 runner 오류는 환경 문제라 에이전트 수정으로 풀 수 없다
        revisable = run.stage != "R0" and verdict.code != PROTECTION_UNAVAILABLE
        outcome.fail(run.stage, verdict.code, verdict.reason, revisable=revisable, **details)
        return False


def _path(exc: PatchDenied) -> dict[str, str]:
    return {"path": exc.path} if exc.path else {}


def patch_gate_from_settings(
    settings: Settings, docker: DockerPort, clock: Clock
) -> PatchGate | None:
    """`RUNNER_IMAGE_ID`가 없으면 None(브로커는 create_pr를 PROTECTION_UNAVAILABLE로 거절한다)."""
    profile = RunnerProfile.from_settings(settings)
    if profile is None:
        return None
    runs_dir = Path(settings.runtime.runs_dir or "runs").resolve()
    return PatchGate(
        policy=load_policy(),
        runner=Runner(docker, profile, clock),
        mirror=runs_dir / "mirror" / MIRROR_NAME,
        runs_dir=runs_dir,
    )
