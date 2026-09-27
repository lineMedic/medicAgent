"""W22 단위 테스트: 등록 repo·알림 route·자동 처리 작성자 catalog와 GitHub 설정.

- 등록 repo·route는 host 설정에서만 온다. 모르는 repo·route·꺼진 route는 거부한다
- `ISSUE_TRUSTED_AUTHOR_IDS`는 숫자 ID 목록만(login 문자열 불가)
- `github.write_enabled` 기본 false(shadow), API 버전은 N11 확정 전 생략(null 없음, D60)
- 포트는 설정의 등록 repo·credential로만 만든다
"""

import copy
import tomllib
from pathlib import Path

import pytest

from linemedic.common.config import ConfigError, LineMedicConfig, load_settings, validate_model
from linemedic.control_plane.catalog import Catalog, CatalogError
from linemedic.integrations.github import GitHubNotConfigured, HttpGitHub, github_from_settings

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG = REPO_ROOT / "config" / "linemedic.toml"
ENV = {
    "GITHUB_REPOSITORY": "demo-team/l3-mes-api",
    "GITHUB_REPOSITORY_ID": "100001",
    "ISSUE_TRUSTED_AUTHOR_IDS": "200001, 200002",
}


def raw_config() -> dict:
    return tomllib.loads(CONFIG.read_text(encoding="utf-8"))


def catalog(env=ENV) -> Catalog:
    return Catalog.from_config(load_settings(CONFIG, env).config)


def test_default_config_is_shadow_mode_without_api_version():
    config = load_settings(CONFIG, {}).config
    assert config.github.write_enabled is False
    assert config.github.api_version is None
    assert config.github.base_url == "https://api.github.com"
    assert config.repository.service_id == "mes-api"


def test_registered_repository_comes_from_env_and_unknown_ids_are_refused():
    entry = catalog().require_repository(100001)
    assert (entry.id, entry.full_name, entry.service_id) == (
        100001,
        "demo-team/l3-mes-api",
        "mes-api",
    )
    with pytest.raises(CatalogError):
        catalog().require_repository(100002)
    with pytest.raises(CatalogError):
        catalog({}).require_repository(100001)  # G2 전: 등록 repo 없음


def test_routes_are_only_registered_and_enabled_ones():
    cat = catalog()
    route = cat.require_route("github-issue-primary")
    assert (route.adapter, route.target) == ("github_comment", "bound_issue")
    with pytest.raises(CatalogError):
        cat.require_route("ops-mail")  # 꺼진 route
    with pytest.raises(CatalogError):
        cat.require_route("https://evil.example/hook")
    with pytest.raises(CatalogError):
        cat.require_route("unknown-route")


def test_trusted_authors_are_numeric_ids_only():
    cat = catalog()
    assert cat.trusted_author_ids == frozenset({200001, 200002})
    assert cat.is_trusted_author(200001) is True
    assert cat.is_trusted_author(200003) is False
    assert cat.is_trusted_author("200001") is False  # login·문자열은 권한 근거가 아니다
    assert cat.is_trusted_author(True) is False
    assert catalog({}).trusted_author_ids == frozenset()  # G2 전: 자동 처리 작성자 없음
    for bad in ("octocat", "200001,octocat", "200001,,200002", "-1"):
        with pytest.raises(ConfigError):
            load_settings(CONFIG, {**ENV, "ISSUE_TRUSTED_AUTHOR_IDS": bad})


@pytest.mark.parametrize("full_name", ["demo-team/..", "../l3-mes-api", "./x", "a/b/c", "a"])
def test_repository_name_from_env_must_be_owner_slash_name(full_name):
    with pytest.raises(ConfigError):
        load_settings(CONFIG, {**ENV, "GITHUB_REPOSITORY": full_name})


def test_deny_labels_are_exposed():
    assert catalog().deny_labels == frozenset({"linemedic-ignore", "needs-human"})


@pytest.mark.parametrize(
    "change",
    [
        lambda c: c["repository"].update(service_id="unknown-service"),
        lambda c: c["repository"].update(service_id="vision-inspection"),  # intake 서비스와 다름
        lambda c: c["notifications"]["routes"]["github-issue-primary"].pop("target"),
        lambda c: c["notifications"]["routes"]["github-issue-primary"].update(
            recipient_config_key="LINEMEDIC_OPS_RECIPIENT"
        ),
        lambda c: c["notifications"]["routes"]["ops-mail"].update(
            recipient_config_key="GITHUB_BROKER_CREDENTIAL"
        ),
        lambda c: c["notifications"]["routes"]["ops-mail"].update(recipient_config_key="X_EMAIL"),
        lambda c: c["notifications"]["routes"].update(
            {"Bad Route": copy.deepcopy(c["notifications"]["routes"]["github-issue-primary"])}
        ),
        lambda c: c["github"].update(base_url="http://api.github.com"),
        lambda c: c["github"].update(api_version="latest"),
        lambda c: c["github"].update(write_enabled="false"),
        lambda c: c["github"].pop("write_enabled"),  # 쓰기 스위치는 기본값 없이 명시해야 한다
        lambda c: c.pop("github"),
    ],
    ids=[
        "repo_service_unknown",
        "repo_service_mismatch",
        "comment_route_without_target",
        "comment_route_with_recipient",
        "mail_route_recipient_not_recipient_secret",
        "mail_route_recipient_not_registered",
        "bad_route_id",
        "github_http",
        "github_api_version_format",
        "github_write_flag_string",
        "github_write_flag_missing",
        "github_missing",
    ],
)
def test_invalid_repository_route_or_github_config_is_rejected(change):
    data = raw_config()
    change(data)
    with pytest.raises(ConfigError):
        validate_model(LineMedicConfig, data, "config")


def test_port_is_built_only_from_settings_repository_and_credential():
    secret = "test-broker-" + "y" * 24  # 테스트 전용 가짜 값
    with pytest.raises(GitHubNotConfigured):
        github_from_settings(load_settings(CONFIG, {}))  # repo·credential 없음
    with pytest.raises(GitHubNotConfigured):
        github_from_settings(load_settings(CONFIG, ENV))  # credential 없음
    port = github_from_settings(load_settings(CONFIG, {**ENV, "GITHUB_BROKER_CREDENTIAL": secret}))
    assert isinstance(port, HttpGitHub)
    assert (port.repository_id, port.full_name, port.write_enabled) == (
        100001,
        "demo-team/l3-mes-api",
        False,
    )
    assert port.api_version is None
    assert secret not in repr(port)
