"""N11 live smoke: 등록 repo 조회·Issue 생성·댓글 각 1회 (W22, G2·G10 필요).

`make test-live`에서만 실행된다. 필수 값이 없으면 통과가 아니라 skip(NOT_CONFIGURED)으로 보고한다.

- 읽기(repo ID 일치·봇 identity·Issue 목록): G2 — `GITHUB_REPOSITORY`·`GITHUB_REPOSITORY_ID`·
  `GITHUB_BROKER_CREDENTIAL`. unit 테스트와 같은 계약 검사(`check_read_contract`)를 쓴다.
- 쓰기(`linemedic-smoke` 라벨 테스트 Issue 1개 + 댓글 1개): 추가로
  G10(config `github.write_enabled=true`)과 이번 실행에 대한 사람의 허락 표시
  `LINEMEDIC_CONFIRM_GITHUB_WRITE=1`이 모두 있어야 한다.
  성공하면 receipt(Issue number·node ID·comment ID)를 `evidence/N11-github-smoke.md`에 남긴다.
"""

import os
from pathlib import Path

import pytest

from linemedic.common.clock import SystemClock, to_rfc3339
from linemedic.common.config import DEFAULT_CONFIG_PATH, load_settings, process_env
from linemedic.integrations.github import GitHubNotConfigured, GitHubResponse, github_from_settings
from linemedic.tests.helpers.github_contract import check_read_contract

EVIDENCE = Path("evidence") / "N11-github-smoke.md"


def _port():
    settings = load_settings(DEFAULT_CONFIG_PATH, process_env())
    try:
        return settings, github_from_settings(settings)
    except GitHubNotConfigured as exc:
        pytest.skip(f"NOT_CONFIGURED (G2): {exc}")


@pytest.mark.live_github
def test_github_read_contract_on_registered_repository():
    _, port = _port()
    observed = check_read_contract(port)
    assert observed["repository_id"] == port.repository_id


@pytest.mark.live_github
def test_github_issue_and_comment_smoke_writes_once():
    settings, port = _port()
    if not settings.config.github.write_enabled:
        pytest.skip("G10 전: config github.write_enabled=false (shadow 모드)")
    if os.environ.get("LINEMEDIC_CONFIRM_GITHUB_WRITE") != "1":
        pytest.skip("이번 실행의 쓰기 허락 표시(LINEMEDIC_CONFIRM_GITHUB_WRITE=1)가 없다")
    issue = port.create_issue(
        "[linemedic-smoke] N11 GitHub 연동 확인",
        "LineMedic N11 smoke 시험이 만든 Issue입니다. 작업 요청이 아닙니다.",
        ["linemedic-smoke"],
    )
    assert isinstance(issue, GitHubResponse) and issue.status == 201
    comment = port.create_issue_comment(issue.data["number"], "N11 smoke 댓글 1회")
    assert isinstance(comment, GitHubResponse) and comment.status == 201
    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE.write_text(
        "# N11 GitHub smoke receipt\n\n"
        f"- 시각(UTC): {to_rfc3339(SystemClock().utc_now())}\n"
        f"- repo: {port.full_name} (ID {port.repository_id})\n"
        f"- Issue: #{issue.data['number']} (node ID {issue.data['node_id']})\n"
        f"- 댓글 ID: {comment.data['id']}\n"
        f"- API 버전 헤더: {port.api_version or '보내지 않음(N11 확정 전)'}\n",
        encoding="utf-8",
    )
