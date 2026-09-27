"""W12 배포 시험 도우미(테스트 전용). 운영 코드에서 쓰지 않는다.

PrWorld(W11)로 봇 PR까지 연 뒤 사람의 리뷰·squash 머지를 흉내 낸다.

- "원격" bare repo(`remote.git`)가 candidate를 받고, 사람이 base 위에 같은 tree의
  squash commit을 만든다
- FakeGitHub에는 봇이 아닌 리뷰어의 candidate head 승인과 merged PR을 넣는다
- 지금 MES container(FakeDocker, 버그 base image)와 신뢰 prober 응답(가짜 MES 집계)을 둔다.
  가짜 MES는 container의 `/data` mount에서 로트를 읽고, 빌드 context의 `app/defects.py`가 고쳐진
  image만 올바르게 집계한다
- ReleaseExecutor는 GitFetcher(file protocol)로 원격에서 merge commit 하나를 신뢰 mirror에 가져온다
"""

import base64
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from linemedic.common.config import load_settings
from linemedic.control_plane.broker.reconcile import ExecutionReconciler
from linemedic.control_plane.broker.runner import Runner
from linemedic.control_plane.catalog import Catalog
from linemedic.control_plane.release import ReleaseExecutor, lock_holder
from linemedic.factory_sim.scenarios import mes_container_options, resource_names
from linemedic.integrations.docker import CommandResult, FakeDocker
from linemedic.integrations.git_fetch import GitFetcher
from linemedic.tests.helpers.api import ROUTE_ID, RUN, make_api
from linemedic.tests.helpers.pr_world import GIT_ENV, PASSING, REPO, REPO_ID, PrWorld
from linemedic.tests.helpers.runner import profile, scripted_docker

REPO_ROOT = Path(__file__).resolve().parents[3]
REVIEWER = 300001
S1_SIGNATURE = {  # detector(W07)가 사건 details에 남기는 signature 모양
    "service": "mes-api",
    "error_type": "KeyError:inspector_id",
    "top_frame": "app.defects:summarize",
    "endpoint": "/defects/summary",
}
S1_ERROR_EVENT = {
    "level": "error",
    "error_type": "KeyError",
    "error_field": "inspector_id",
    "top_frame": "app.defects:summarize",
    "path": "/defects/summary",
}
BASE_IMAGE = "sha256:" + "1" * 64
FIX_MARK = 'row.get("inspector_id") or "미지정"'
RELEASE_CONFIG = load_settings(
    REPO_ROOT / "config" / "linemedic.toml",
    {"GITHUB_REPOSITORY": REPO, "GITHUB_REPOSITORY_ID": str(REPO_ID)},
).config
REVIEWER_ENV = {
    **GIT_ENV,
    "GIT_AUTHOR_NAME": "Reviewer",
    "GIT_AUTHOR_EMAIL": "reviewer@example.invalid",
    "GIT_AUTHOR_DATE": "@1790000000 +0000",
    "GIT_COMMITTER_NAME": "Reviewer",
    "GIT_COMMITTER_EMAIL": "reviewer@example.invalid",
    "GIT_COMMITTER_DATE": "@1790000000 +0000",
}


def git(*args: str, env: dict[str, str] = GIT_ENV, stdin: str | None = None) -> str:
    return subprocess.run(
        ["git", *args], env=env, input=stdin, capture_output=True, text=True, check=True
    ).stdout.strip()


def summarize(records: list[dict[str, Any]], fixed: bool) -> dict[str, Any]:
    by_inspector: dict[str, int] = {}
    for row in records:
        inspector = (row.get("inspector_id") or "미지정") if fixed else row["inspector_id"]
        by_inspector[inspector] = by_inspector.get(inspector, 0) + 1
    return {"total_defects": len(records), "by_inspector": by_inspector}


class FakeMes:
    """prober(`docker exec ... python -c PROBE_CODE <url>`)에 MES처럼 답한다."""

    def __init__(self, docker: FakeDocker) -> None:
        self.docker = docker
        self.fixed_images: set[str] = set()
        self.wrong_answers = False  # 틀린 200(거짓 정상)
        self.log_recurrence = False  # 업무 요청마다 S1과 같은 오류 로그를 남긴다
        self.requests: list[str] = []

    def __call__(self, name: str, command: list[str]) -> CommandResult:
        url = urlparse(command[3])
        self.requests.append(command[3])
        container = self.docker.containers.get(url.hostname or "")
        if container is None:
            return CommandResult(0, json.dumps({"error": "gaierror"}) + "\n", "")
        status, body = 200, {"status": "ok"}
        if url.path == "/defects/summary":
            lot = parse_qs(url.query)["lot_id"][0]
            if self.log_recurrence:
                self.docker.logs_follow(url.hostname or "").push(json.dumps(S1_ERROR_EVENT))
            source = next(m["Source"] for m in container["Mounts"] if m["Destination"] == "/data")
            path = Path(source) / "lots" / f"{lot}.json"
            if not path.is_file():
                status, body = 404, {"detail": "lot not found"}
            elif self.wrong_answers:
                body = {"lot_id": lot, "total_defects": 0, "by_inspector": {}}
            else:
                records = json.loads(path.read_text(encoding="utf-8"))["records"]
                try:
                    fixed = container["Image"] in self.fixed_images
                    body = {"lot_id": lot, **summarize(records, fixed)}
                except KeyError:
                    status, body = 500, {"detail": "Internal Server Error"}
        encoded = base64.b64encode(json.dumps(body, ensure_ascii=False).encode()).decode()
        return CommandResult(0, json.dumps({"status": status, "body": encoded}) + "\n", "")


class ReleaseWorld:
    """봇 PR이 열리고(PR_OPENED·WAITING_REVIEW) 사람이 candidate를 승인한 상태.

    `merge()`로 사람의 squash 머지를 흉내 낸다.
    """

    def __init__(self, store, conn, clock, seed: tuple[Path, str], tmp_path: Path) -> None:
        seed_mirror, base = seed
        self.mirror = tmp_path / "mirror" / "l3-mes-api.git"  # 테스트마다 새 신뢰 mirror
        shutil.copytree(seed_mirror, self.mirror)
        self.runs_dir = tmp_path / "runs"
        self.store, self.conn, self.clock, self.base = store, conn, clock, base
        self.pr = PrWorld(store, conn, clock, (self.mirror, base), self.runs_dir)
        self.github = self.pr.github
        self.incident, self.work = self.pr.incident, self.pr.work
        conn.execute(
            "UPDATE incidents SET details_json = ? WHERE id = ?",
            (json.dumps({"signature": S1_SIGNATURE}), self.incident),
        )
        self.proposal_id = self.pr.run()
        _, record = self.pr.proposal(self.proposal_id)
        self.candidate_sha = record["candidate"]["candidate_sha"]
        self.candidate_tree = record["candidate"]["candidate_tree"]
        pr_execution = self.pr.execution()
        self.pr_number = json.loads(pr_execution["result_json"])["pr_number"]
        self.head = json.loads(pr_execution["request_json"])["head"]

        # 원격: 봇이 push한 candidate 브랜치
        self.remote = tmp_path / "remote.git"
        git("clone", "--quiet", "--bare", str(self.mirror), str(self.remote))
        workdir = self.runs_dir / RUN / "checkouts" / self.proposal_id / "1" / "repo.git"
        git(
            f"--git-dir={self.remote}",
            "fetch",
            "--quiet",
            str(workdir),
            f"{self.candidate_sha}:refs/heads/{self.head}",
        )
        self.review = self.github.add_review(
            self.pr_number, reviewer_id=REVIEWER, commit_id=self.candidate_sha
        )
        self.merge_sha: str | None = None

        # 지금 MES(버그 base image)
        names = resource_names(RUN)
        self.mes, self.network = names["container"], names["network"]
        self.outcomes = dict(PASSING)
        self.docker = scripted_docker(self.outcomes)
        scripted = self.docker.run_handler
        assert scripted is not None

        def handler(name: str, options: list[str], command: list[str] | None) -> None:
            if name.startswith("lm-"):  # R0·R1·R2 runner
                scripted(name, options, command)

        self.docker.run_handler = handler
        self.fake_mes = FakeMes(self.docker)
        self.docker.exec_handler = self.fake_mes
        real_build = self.docker.build

        def build(context, dockerfile, tag, build_args=None):
            image = real_build(context, dockerfile, tag, build_args)
            if FIX_MARK in (Path(context) / "app" / "defects.py").read_text(encoding="utf-8"):
                self.fake_mes.fixed_images.add(image)
            return image

        self.docker.build = build  # type: ignore[method-assign]
        self.docker.images[BASE_IMAGE] = BASE_IMAGE
        self.docker.networks.add(self.network)
        self.s1_data = tmp_path / "s1-data"
        (self.s1_data / "lots").mkdir(parents=True)
        self.previous_id = self.docker.run(
            mes_container_options(self.mes, RUN, self.network, self.s1_data), BASE_IMAGE
        )
        self.docker.calls.clear()

        self.catalog = Catalog.from_config(RELEASE_CONFIG)
        self.jobs: list[Any] = []
        self.executor = ReleaseExecutor(
            store,
            port=self.github,
            catalog=self.catalog,
            docker=self.docker,
            runner=Runner(self.docker, profile(), clock),
            mirror=self.mirror,
            runs_dir=self.runs_dir,
            clock=clock,
            route_id=ROUTE_ID,
            fetcher=GitFetcher(str(self.remote), None, protocols=("file",)),
            dispatch=self.jobs.append,
        )
        self.reconciler = ExecutionReconciler(
            store, opener=self.pr.opener, route_id=ROUTE_ID, release=self.executor
        )
        self.api = make_api(
            store,
            conn,
            catalog=self.catalog,
            release_executor=self.executor,
            execution_reconciler=self.reconciler,
        )

    # 사람의 머지

    def squash(self, tree: str | None = None) -> str:
        """원격 baseline에 squash commit을 만든다(`tree`가 없으면 candidate tree)."""
        merge_sha = git(
            f"--git-dir={self.remote}",
            "commit-tree",
            tree or self.candidate_tree,
            "-p",
            self.base,
            "-m",
            f"LineMedic 수정 제안 (#{self.pr_number})",
            env=REVIEWER_ENV,
        )
        git(f"--git-dir={self.remote}", "update-ref", f"refs/heads/baseline/{RUN}", merge_sha)
        return merge_sha

    def merge(self, tree: str | None = None) -> str:
        self.merge_sha = self.squash(tree)
        self.github.merge_pull(self.pr_number, self.merge_sha, tree or self.candidate_tree)
        return self.merge_sha

    def tree_with_human_change(self) -> str:
        """candidate tree에 사람이 파일 하나를 더한 tree(리뷰 뒤 추가 수정)."""
        blob = git(
            f"--git-dir={self.remote}", "hash-object", "-w", "--stdin", stdin="# 사람 추가 수정\n"
        )
        index = {**GIT_ENV, "GIT_INDEX_FILE": str(self.remote / "human.index")}
        git(f"--git-dir={self.remote}", "read-tree", self.candidate_tree, env=index)
        git(
            f"--git-dir={self.remote}",
            "update-index",
            "--add",
            "--cacheinfo",
            f"100644,{blob},app/extra.py",
            env=index,
        )
        return git(f"--git-dir={self.remote}", "write-tree", env=index)

    # 요청

    def body(self, **overrides: Any) -> dict[str, Any]:
        body = {
            "schema_version": "linemedic.v4",
            "run_id": RUN,
            "incident_id": self.incident,
            "work_id": self.work,
            "proposal_id": self.proposal_id,
            "pr_number": self.pr_number,
            "approved_merge_sha": self.merge_sha,
            "expected_incident_version": self.incident_row()["version"],
            "expected_current_image_id": BASE_IMAGE,
            "approval_note": "diff·근거·허용 파일 범위를 확인하고 머지했다",
        }
        body.update(overrides)
        return body

    def approve(
        self,
        key: str = "release-1",
        headers: dict | None = None,
        body: dict | None = None,
        **overrides: Any,
    ):
        return self.api.client.post(
            "/ops/releases",
            json=body if body is not None else self.body(**overrides),
            headers={**(headers or self.api.operator), "Idempotency-Key": key},
        )

    def drain(self) -> list[Any]:
        """dispatch된 배포·검증을 이 thread에서 끝까지 돌린다."""
        results = []
        while self.jobs:
            results.append(self.jobs.pop(0)())
        return results

    # 조회

    def incident_row(self) -> Any:
        return self.pr.incident_row()

    def work_row(self) -> Any:
        return self.pr.work_row()

    def deploy(self) -> Any:
        return self.conn.execute("SELECT * FROM executions WHERE operation = 'DEPLOY'").fetchone()

    def deploys(self) -> list[Any]:
        return self.conn.execute("SELECT * FROM executions WHERE operation = 'DEPLOY'").fetchall()

    def lock(self) -> str | None:
        with self.store.read() as tx:
            return lock_holder(tx, RUN)

    def mes_runs(self) -> list[tuple]:
        return [c for c in self.docker.calls if c[0] == "run" and c[1][1] == self.mes]

    def audit_types(self) -> list[str]:
        return [
            row[0]
            for row in self.conn.execute(
                "SELECT event_type FROM audit_events WHERE incident_id = ? ORDER BY seq",
                (self.incident,),
            )
        ]
