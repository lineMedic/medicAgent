"""W22 단위 테스트: GitHubPort 계약 — FakeGitHub(장애 주입)와 HttpGitHub(httpx MockTransport).

- 목록 3페이지·PR 섞임·두 번째 페이지 403·생성 뒤 timeout(부작용 있음)이
  정확한 오류 타입으로 드러난다
- 등록 repo 밖 요청을 만들 수 없다(메서드에 repo 인자 없음, 경로는 항상 `/repos/<등록 repo>`)
- `write_enabled=false`면 쓰기 요청 0회, 계획(`WritePlan`)만 돌려준다
- credential·Authorization 헤더가 오류 메시지·repr에 나오지 않는다
"""

import inspect
import json
from datetime import UTC, datetime

import httpx
import pytest

from linemedic.common.clock import FakeClock
from linemedic.integrations import github
from linemedic.integrations.github import (
    Conflict,
    FakeGitHub,
    Forbidden,
    HttpGitHub,
    NotFound,
    RateLimited,
    Unknown,
    WritePlan,
)
from linemedic.tests.helpers.github_contract import check_read_contract

REPO = "demo-team/l3-mes-api"
REPO_ID = 100001
CREDENTIAL = "test-broker-credential-" + "x" * 20  # 테스트 전용 가짜 값
PORT_METHODS = (
    "get_repo",
    "get_identity",
    "list_issues",
    "get_issue",
    "create_issue",
    "list_issue_comments",
    "create_issue_comment",
    "list_pulls",
    "get_pull",
    "create_pull",
)


def fake(**kwargs) -> FakeGitHub:
    clock = FakeClock(start=datetime(2026, 9, 27, 0, 0, 0, tzinfo=UTC))
    return FakeGitHub(REPO_ID, REPO, clock=clock, **kwargs)


# ── 공통 계약 (live에서는 HttpGitHub에 같은 검사를 적용한다: tests/live/test_github_smoke.py) ──


def test_fake_satisfies_read_contract():
    port = fake()
    port.add_issue(title="외부 Issue")
    port.add_issue(title="PR 항목", is_pull=True)
    observed = check_read_contract(port)
    assert (observed["first_page_items"], observed["first_page_pull_requests"]) == (2, 1)


def test_port_methods_take_no_repository_argument():
    for name in PORT_METHODS:
        params = inspect.signature(getattr(github.GitHubBase, name)).parameters
        assert not {"repo", "owner", "repository", "full_name", "url", "path"} & set(params), name


# ── FakeGitHub: 목록·페이지·PR 섞임 ───────────────────────────


def test_three_pages_include_pull_requests():
    port = fake()
    for i in range(250):
        port.add_issue(title=f"#{i}", is_pull=(i % 10 == 0))
    pages = [port.list_issues(per_page=100, page=p) for p in (1, 2, 3)]
    assert [len(p.data) for p in pages] == [100, 100, 50]
    assert [p.has_next for p in pages] == [True, True, False]
    items = [item for p in pages for item in p.data]
    assert len({item["number"] for item in items}) == 250
    assert sum("pull_request" in item for item in items) == 25  # Issues endpoint에 PR이 섞인다


def test_second_page_forbidden_is_forbidden_and_other_pages_still_work():
    port = fake()
    for i in range(250):
        port.add_issue(title=f"#{i}")
    port.fail_next("forbidden", when=lambda r: r.params.get("page") == 2)
    assert len(port.list_issues(page=1).data) == 100
    with pytest.raises(Forbidden):
        port.list_issues(page=2)
    assert len(port.list_issues(page=3).data) == 50
    assert len(port.list_issues(page=2).data) == 100  # 장애는 한 번만


def test_since_filters_by_update_time_and_etag_gives_304():
    port = fake()
    port.add_issue(title="old")
    port.clock.advance(3600)
    newer = port.add_issue(title="new")
    page = port.list_issues(since=newer["updated_at"])
    assert [item["title"] for item in page.data] == ["new"]
    assert page.etag
    again = port.list_issues(since=newer["updated_at"], etag=page.etag)
    assert (again.status, again.data) == (304, None)


# ── FakeGitHub: 쓰기·부작용·오류 타입 ─────────────────────────


def test_write_disabled_makes_no_write_calls():
    port = fake(write_enabled=False)
    issue = port.add_issue(title="외부 Issue")
    plans = [
        port.create_issue("제목", "본문", ["linemedic"]),
        port.create_issue_comment(issue["number"], "댓글"),
        port.create_pull("autofix/r-20260927-020000-abcd/INC-1", "main", "제목", "본문"),
    ]
    assert all(isinstance(plan, WritePlan) for plan in plans)
    assert [plan.path for plan in plans] == [
        f"/repos/{REPO}/issues",
        f"/repos/{REPO}/issues/{issue['number']}/comments",
        f"/repos/{REPO}/pulls",
    ]
    assert port.write_calls == 0
    assert [r for r in port.requests if r.method != "GET"] == []
    assert len(port.issues) == 1 and port.comments == {} and port.pulls == {}


def test_write_enabled_creates_issue_comment_and_pull_in_shared_number_space():
    port = fake(write_enabled=True)
    issue = port.create_issue("제목", "본문", ["linemedic"]).data
    assert issue["labels"] == [{"name": "linemedic"}] and issue["user"]["login"] == "linemedic-bot"
    comment = port.create_issue_comment(issue["number"], "댓글").data
    assert port.list_issue_comments(issue["number"]).data == [comment]
    pull = port.create_pull("autofix/r-1/INC-1/PROP-1", "baseline/r-1", "PR 제목", "PR 본문").data
    assert pull["number"] == issue["number"] + 1
    assert port.get_pull(pull["number"]).data["head"]["ref"] == "autofix/r-1/INC-1/PROP-1"
    listed = port.list_pulls(head="demo-team:autofix/r-1/INC-1/PROP-1", state="open").data
    assert [p["number"] for p in listed] == [pull["number"]]
    assert "pull_request" in port.get_issue(pull["number"]).data
    assert port.write_calls == 3
    assert all(r.path.startswith(f"/repos/{REPO}") or r.path == "/user" for r in port.requests)


def test_timeout_after_side_effect_is_unknown_and_the_issue_exists():
    port = fake(write_enabled=True)
    port.fail_next("timeout", after_side_effect=True)
    with pytest.raises(Unknown) as caught:
        port.create_issue("제목", "본문")
    assert caught.value.observation == "timeout"
    assert len(port.issues) == 1  # 요청은 반영됐다 → 재시도 전에 조회해야 한다


def test_timeout_before_side_effect_is_unknown_without_issue():
    port = fake(write_enabled=True)
    port.fail_next("timeout", after_side_effect=False)
    with pytest.raises(Unknown):
        port.create_issue("제목", "본문")
    assert port.issues == {}


@pytest.mark.parametrize(
    ("kind", "error"),
    [
        ("forbidden", Forbidden),
        ("rate_limited", RateLimited),
        ("not_found", NotFound),
        ("conflict", Conflict),
        ("server_error", Unknown),
        ("disconnect", Unknown),
        ("connect_failed", Unknown),
    ],
)
def test_each_injected_failure_has_its_error_type(kind, error):
    port = fake()
    port.fail_next(kind)
    with pytest.raises(error):
        port.get_repo()
    assert port.get_repo().status == 200


def test_rate_limited_carries_retry_after_and_connect_failure_is_not_sent():
    port = fake()
    port.fail_next("rate_limited")
    with pytest.raises(RateLimited) as caught:
        port.get_repo()
    assert caught.value.retry_after == 60
    port.fail_next("connect_failed")
    with pytest.raises(Unknown) as unknown:
        port.get_repo()
    assert unknown.value.request_sent is False
    with pytest.raises(ValueError):
        port.fail_next("connect_failed", after_side_effect=True)  # 보내지 않은 요청은 부작용이 없다


def test_repo_renamed_to_another_id_is_visible():
    port = fake(actual_repository_id=999)
    assert port.get_repo().data["id"] == 999  # 호출자가 등록 ID와 비교해 멈춘다


# ── 입력 검증: 비신뢰 값을 경로에 잇지 않는다 ─────────────────


@pytest.mark.parametrize("number", [0, -1, True, "7", 7.0, None])
def test_numbers_must_be_positive_ints(number):
    port = fake()
    with pytest.raises(ValueError):
        port.get_issue(number)
    assert port.requests == []


@pytest.mark.parametrize(
    "call",
    [
        lambda p: p.create_pull("../main", "main", "t", "b"),
        lambda p: p.create_pull("autofix/../main", "main", "t", "b"),
        lambda p: p.list_pulls(head="demo-team:a..b"),
        lambda p: p.create_pull("feature x", "main", "t", "b"),
        lambda p: p.create_pull("a//b", "main", "t", "b"),
        lambda p: p.create_pull("ok", "main.lock", "t", "b"),
        lambda p: p.list_pulls(head="demo-team:../x"),
        lambda p: p.list_pulls(head="no-owner-branch"),
        lambda p: p.list_pulls(state="merged"),
        lambda p: p.list_issues(state="everything"),
        lambda p: p.list_issues(per_page=101),
        lambda p: p.list_issues(page=0),
        lambda p: p.list_issues(since="yesterday"),
        lambda p: p.create_issue("", "b"),
        lambda p: p.create_issue("t" * 257, "b"),
        lambda p: p.create_issue("t", "b" * 65537),
        lambda p: p.create_issue("t", "b", ["x"] * 11),
        lambda p: p.create_issue("t", "b", ["l" * 51]),
    ],
)
def test_invalid_arguments_are_rejected_before_any_request(call):
    port = fake(write_enabled=True)
    with pytest.raises(ValueError):
        call(port)
    assert port.requests == []


@pytest.mark.parametrize(
    ("repository_id", "full_name"),
    [(0, REPO), (REPO_ID, "../x"), (REPO_ID, "a/b/c"), (REPO_ID, "demo-team/.."), (True, REPO)],
)
def test_registered_repository_must_be_well_formed(repository_id, full_name):
    with pytest.raises(ValueError):
        FakeGitHub(repository_id, full_name)


# ── HttpGitHub (httpx MockTransport) ──────────────────────────


class Recorder:
    def __init__(self, responder):
        self.requests: list[httpx.Request] = []
        self.responder = responder

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.responder(request)


def http(responder, **kwargs) -> tuple[HttpGitHub, Recorder]:
    recorder = Recorder(responder)
    port = HttpGitHub(REPO_ID, REPO, CREDENTIAL, transport=httpx.MockTransport(recorder), **kwargs)
    return port, recorder


def ok(data, **headers) -> httpx.Response:
    return httpx.Response(200, json=data, headers=headers)


def test_http_uses_registered_repo_path_and_headers():
    port, rec = http(lambda r: ok({"id": REPO_ID, "full_name": REPO}))
    assert port.get_repo().data["id"] == REPO_ID
    request = rec.requests[0]
    assert (request.method, str(request.url)) == ("GET", f"https://api.github.com/repos/{REPO}")
    assert request.headers["authorization"] == f"Bearer {CREDENTIAL}"
    assert request.headers["accept"] == "application/vnd.github+json"
    assert "x-github-api-version" not in request.headers  # N11 확정 전에는 보내지 않는다

    versioned, rec2 = http(lambda r: ok({}), api_version="2022-11-28")
    versioned.get_identity()
    assert str(rec2.requests[0].url) == "https://api.github.com/user"
    assert rec2.requests[0].headers["x-github-api-version"] == "2022-11-28"


def test_http_list_issues_params_link_etag_and_rate():
    link = f'<https://api.github.com/repos/{REPO}/issues?page=3>; rel="next", <x>; rel="last"'
    port, rec = http(
        lambda r: ok(
            [{"number": 1, "state": "open"}],
            link=link,
            etag='W/"abc"',
            **{"x-ratelimit-remaining": "4999", "x-ratelimit-reset": "1790000000"},
        )
    )
    page = port.list_issues(since="2026-09-27T00:00:00.000000Z", page=2, etag='W/"old"')
    params = dict(rec.requests[0].url.params)
    assert params == {
        "state": "all",
        "since": "2026-09-27T00:00:00.000000Z",
        "sort": "updated",
        "direction": "asc",
        "per_page": "100",
        "page": "2",
    }
    assert rec.requests[0].headers["if-none-match"] == 'W/"old"'
    assert page.has_next is True and page.etag == 'W/"abc"'
    assert (page.rate.remaining, page.rate.reset) == (4999, 1790000000)

    last, _ = http(lambda r: ok([]))
    assert last.list_issues().has_next is False


def test_http_not_modified_is_a_response_not_an_error():
    port, _ = http(lambda r: httpx.Response(304, headers={"etag": 'W/"abc"'}))
    page = port.list_issues(etag='W/"abc"')
    assert (page.status, page.data, page.etag) == (304, None, 'W/"abc"')


@pytest.mark.parametrize(
    ("status", "headers", "body", "error"),
    [
        (401, {}, {"message": "Bad credentials"}, Forbidden),
        (403, {}, {"message": "Resource not accessible by integration"}, Forbidden),
        (403, {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1790000000"}, {}, RateLimited),
        (403, {"retry-after": "30"}, {"message": "secondary"}, RateLimited),
        (403, {}, {"message": "You have exceeded a secondary rate limit"}, RateLimited),
        (429, {"retry-after": "5"}, {}, RateLimited),
        (404, {}, {"message": "Not Found"}, NotFound),
        (410, {}, {"message": "Gone"}, NotFound),
        (409, {}, {"message": "Conflict"}, Conflict),
        (422, {}, {"message": "Validation Failed"}, Conflict),
        (500, {}, {}, Unknown),
        (502, {}, {}, Unknown),
    ],
)
def test_http_status_mapping(status, headers, body, error):
    port, _ = http(lambda r: httpx.Response(status, json=body, headers=headers))
    with pytest.raises(error) as caught:
        port.get_repo()
    assert CREDENTIAL not in str(caught.value) and CREDENTIAL not in repr(caught.value)


def test_http_error_message_is_masked_and_bounded():
    leaked = "ghp_" + "Z9" * 20  # 테스트 전용 가짜 token 형태
    body = {"message": f"echo {leaked} " + "x" * 500}
    port, _ = http(lambda r: httpx.Response(403, json=body))
    with pytest.raises(Forbidden) as caught:
        port.get_repo()
    assert leaked not in str(caught.value)
    assert len(caught.value.message) <= github.MAX_ERROR_MESSAGE_CHARS


def test_http_rate_limit_retry_after_value():
    port, _ = http(lambda r: httpx.Response(429, json={}, headers={"retry-after": "5"}))
    with pytest.raises(RateLimited) as caught:
        port.get_repo()
    assert caught.value.retry_after == 5


@pytest.mark.parametrize(
    ("exc", "sent"),
    [
        (httpx.ConnectError("refused"), False),
        (httpx.ConnectTimeout("slow"), False),
        (httpx.ReadTimeout("slow"), True),
        (httpx.WriteTimeout("slow"), None),
        (httpx.RemoteProtocolError("closed"), None),
    ],
)
def test_http_transport_failures_are_unknown_with_send_state(exc, sent):
    def raise_(request):
        raise exc

    port, _ = http(raise_, write_enabled=True)
    with pytest.raises(Unknown) as caught:
        port.create_issue_comment(7, "댓글")
    assert caught.value.request_sent is sent
    assert caught.value.__cause__ is None and caught.value.__suppress_context__


def test_http_success_with_non_json_body_is_unknown():
    port, _ = http(lambda r: httpx.Response(200, text="<html>proxy</html>"))
    with pytest.raises(Unknown):
        port.get_repo()


def test_http_write_disabled_sends_nothing_and_enabled_sends_json():
    port, rec = http(lambda r: ok({}))
    plan = port.create_issue("제목", "본문", ["linemedic-smoke"])
    assert plan == WritePlan(
        "POST",
        f"/repos/{REPO}/issues",
        {"title": "제목", "body": "본문", "labels": ["linemedic-smoke"]},
    )
    assert rec.requests == [] and port.write_calls == 0

    enabled, rec2 = http(lambda r: httpx.Response(201, json={"number": 9}), write_enabled=True)
    assert enabled.create_issue_comment(9, "댓글").data == {"number": 9}
    request = rec2.requests[0]
    assert (request.method, request.url.path) == ("POST", f"/repos/{REPO}/issues/9/comments")
    assert json.loads(request.content) == {"body": "댓글"}
    assert enabled.write_calls == 1


def test_http_credential_is_not_in_repr_and_is_required():
    port, _ = http(lambda r: ok({}))
    assert CREDENTIAL not in repr(port) and CREDENTIAL not in str(port)
    with pytest.raises(ValueError):
        HttpGitHub(REPO_ID, REPO, "")
    with pytest.raises(ValueError):
        HttpGitHub(REPO_ID, REPO, CREDENTIAL, base_url="http://api.github.com")


def test_http_pull_head_filter_goes_to_query_not_path():
    port, rec = http(lambda r: ok([]))
    port.list_pulls(head="demo-team:autofix/r-1/INC-1/PROP-1", state="all")
    request = rec.requests[0]
    assert request.url.path == f"/repos/{REPO}/pulls"
    assert dict(request.url.params) == {
        "head": "demo-team:autofix/r-1/INC-1/PROP-1",
        "state": "all",
    }
