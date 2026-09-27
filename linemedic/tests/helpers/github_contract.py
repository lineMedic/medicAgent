"""GitHubPort 공통 계약 검사 (W22). unit은 FakeGitHub, live는 HttpGitHub에 같은 검사를 적용한다."""

from linemedic.integrations.github import GitHubPort


def check_read_contract(port: GitHubPort) -> dict:
    """등록 repo ID·봇 identity·Issue 목록 모양을 확인하고 관찰값을 돌려준다(쓰기 없음)."""
    repo = port.get_repo().data
    assert (repo["id"], repo["full_name"]) == (port.repository_id, port.full_name)
    identity = port.get_identity().data
    assert isinstance(identity["login"], str) and isinstance(identity["id"], int)
    page = port.list_issues(state="all", per_page=100, page=1)
    assert page.status == 200 and isinstance(page.data, list)
    for item in page.data:
        assert isinstance(item["number"], int) and item["state"] in ("open", "closed")
    return {
        "repository_id": repo["id"],
        "bot_login": identity["login"],
        "bot_id": identity["id"],
        "first_page_items": len(page.data),
        "first_page_pull_requests": sum("pull_request" in item for item in page.data),
        "has_next": page.has_next,
    }
