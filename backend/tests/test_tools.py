"""GitHub 工具（agent/tools/github.py）测试：httpx MockTransport，无真实网络。"""

from __future__ import annotations

import base64

import httpx
import pytest

from app.agent.tools.github import GitHubClient, parse_repo_url
from app.core.errors import AppError


# --------------------------------------------------------------------------- #
# 仓库地址解析（SRS I3）
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("url", "owner", "repo"),
    [
        ("https://github.com/octocat/Hello-World", "octocat", "Hello-World"),
        ("https://github.com/octocat/Hello-World/", "octocat", "Hello-World"),
        ("https://github.com/user.name/repo.name", "user.name", "repo.name"),
        # SRS I3 正则的 [\w.-]+ 覆盖带点名字；粘贴克隆地址时常见的 .git 后缀剥掉
        ("https://github.com/octocat/Hello-World.git", "octocat", "Hello-World"),
    ],
)
def test_parse_repo_url_ok(url: str, owner: str, repo: str):
    assert parse_repo_url(url) == (owner, repo)


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/octocat/Hello-World",  # 必须 https
        "https://gitee.com/octocat/Hello-World",  # 仅支持 GitHub
        "https://github.com/octocat/Hello-World/tree/main",  # 不接受子路径
        "github.com/octocat/Hello-World",  # 缺协议
        "",
        "not a url",
    ],
)
def test_parse_repo_url_rejected(url: str):
    with pytest.raises(AppError) as exc_info:
        parse_repo_url(url)
    assert exc_info.value.code == 40000


def test_parse_repo_url_owner_repo():
    owner, repo = parse_repo_url("https://github.com/octocat/Hello-World/")
    assert (owner, repo) == ("octocat", "Hello-World")


# --------------------------------------------------------------------------- #
# REST 客户端（MockTransport）
# --------------------------------------------------------------------------- #
def _client(handler) -> GitHubClient:
    return GitHubClient(transport=httpx.MockTransport(handler))


def test_get_repo_ok():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/octocat/Hello-World"
        return httpx.Response(200, json={
            "full_name": "octocat/Hello-World",
            "html_url": "https://github.com/octocat/Hello-World",
            "stargazers_count": 2100,
            "forks_count": 1800,
            "language": None,
            "topics": ["demo"],
            "default_branch": "master",
            "pushed_at": "2026-01-02T03:04:05Z",
        })

    with _client(handler) as gh:
        meta = gh.get_repo("octocat", "Hello-World")
    assert meta["full_name"] == "octocat/Hello-World"
    assert meta["stars"] == 2100 and meta["default_branch"] == "master"


def test_get_repo_404_maps_42201():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Not Found"})

    with _client(handler) as gh:
        with pytest.raises(AppError) as exc_info:
            gh.get_repo("octocat", "no-such-repo")
    assert exc_info.value.code == 42201  # SRS O4：仓库不存在或非公开


def test_rate_limited_403_maps_42901():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "rate limit exceeded"},
                              headers={"x-ratelimit-remaining": "0"})

    with _client(handler) as gh:
        with pytest.raises(AppError) as exc_info:
            gh.get_repo("octocat", "Hello-World")
    assert exc_info.value.code == 42901


def test_timeout_maps_50401():
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out")

    with _client(handler) as gh:
        with pytest.raises(AppError) as exc_info:
            gh.get_repo("octocat", "Hello-World")
    assert exc_info.value.code == 50401


def test_get_user():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/users/octocat"
        return httpx.Response(200, json={
            "login": "octocat", "name": "The Octocat", "bio": None,
            "public_repos": 8, "followers": 9000,
            "html_url": "https://github.com/octocat",
            "created_at": "2011-01-25T18:44:36Z",
        })

    with _client(handler) as gh:
        user = gh.get_user("octocat")
    assert user["login"] == "octocat" and user["public_repos"] == 8


def test_get_languages_percentage():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"Python": 900, "JavaScript": 100})

    with _client(handler) as gh:
        ratio = gh.get_languages("octocat", "Hello-World")
    assert ratio == {"Python": 90.0, "JavaScript": 10.0}


def test_get_readme_decodes_base64():
    payload = {"content": base64.b64encode("# Hello\n世界".encode()).decode(),
               "encoding": "base64", "size": 13}
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    with _client(handler) as gh:
        text = gh.get_readme("octocat", "Hello-World", max_bytes=1024)
    assert text == "# Hello\n世界"


def test_get_readme_missing_returns_none():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Not Found"})

    with _client(handler) as gh:
        assert gh.get_readme("octocat", "Hello-World", max_bytes=1024) is None


def test_list_tree_truncates():
    tree = [{"path": f"src/file{i}.py", "type": "blob", "size": 10} for i in range(10)]
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"tree": tree, "truncated": False})

    with _client(handler) as gh:
        entries = gh.list_tree("octocat", "Hello-World", "main", max_entries=5)
    assert len(entries) == 5
    assert entries[0]["path"] == "src/file0.py"


def test_read_file_decodes_and_404_none():
    content = base64.b64encode("print('hi')".encode()).decode()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/contents/missing.py"):
            return httpx.Response(404, json={"message": "Not Found"})
        return httpx.Response(200, json={"content": content, "encoding": "base64", "size": 12})

    with _client(handler) as gh:
        assert gh.read_file("octocat", "Hello-World", "main.py", max_bytes=1024) == "print('hi')"
        assert gh.read_file("octocat", "Hello-World", "missing.py", max_bytes=1024) is None


def test_get_contributions():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[
            {"sha": "a" * 40, "author": {"login": "octocat"},
             "commit": {"author": {"name": "The Octocat", "date": "2026-02-01T00:00:00Z"},
                        "message": "init"}},
            {"sha": "b" * 40, "author": None,
             "commit": {"author": {"name": "Bob", "date": "2026-01-01T00:00:00Z"},
                        "message": "fix"}},
        ])

    with _client(handler) as gh:
        result = gh.get_contributions("octocat", "Hello-World")
    assert result["count"] == 2
    assert result["authors"] == {"octocat": 1, "Bob": 1}
    assert result["last_commit_at"] == "2026-02-01T00:00:00Z"


def test_json_body_sent_with_headers():
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(200, json={})

    with GitHubClient(token="tok123", transport=httpx.MockTransport(handler)) as gh:
        gh._request("GET", "/user")
    assert seen.get("authorization") == "Bearer tok123"
    assert "intervieweragent" in seen.get("user-agent", "").lower()
