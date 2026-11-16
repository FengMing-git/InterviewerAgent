"""GitHub REST 工具（Plan §3：agent/tools/github.py；模块 M14 的一部分）。

设计：
  * ``GitHubClient`` 封装 REST 调用（httpx 同步），供项目分析流水线与 P2 核验 Agent 复用；
  * 错误统一抛 ``AppError``：
      404（仓库/用户/文件不存在）→ 40401 或 42201（仓库不存在或私有，SRS O4）
      403/429 且限流 → 42901（调用方据此保留已有数据、标 partial）
      网络超时/连接失败 → 50401
  * ``parse_repo_url`` 按 SRS I3 的正则校验并拆 owner/repo，不合法 → 40000；
  * 传 ``transport=httpx.MockTransport(...)`` 可注入测试替身（test_tools.py）。

隐私纪律：只访问仓库公开数据；不带任何用户个人信息。
"""

from __future__ import annotations

import base64
import binascii
import re
from typing import Any

import httpx

from app.config import HTTP_TIMEOUT, settings
from app.core.errors import (
    NOT_FOUND,
    PARAM_INVALID,
    RATE_LIMIT,
    REPO_INACCESSIBLE,
    UPSTREAM_TIMEOUT,
    AppError,
)
from app.core.logger import get_logger

log = get_logger(__name__)

GITHUB_API_BASE = "https://api.github.com"
GITHUB_WEB_BASE = "https://github.com"

# SRS I3：仅公开仓库 1~3 个，URL 形如 https://github.com/octocat/Hello-World
REPO_URL_PATTERN = re.compile(r"^https://github\.com/[\w.-]+/[\w.-]+/?$")

_REPO_URL_CLEAN_RE = re.compile(r"^https?://github\.com/([\w.-]+)/([\w.-]+?)(?:\.git)?/?$")


def parse_repo_url(url: str) -> tuple[str, str]:
    """校验仓库地址并返回 (owner, repo)；不合法抛 40000。

    只接受 https://github.com/owner/repo 形式（SRS I3 正则，允许尾斜杠；
    尾部 .git 视为克隆地址后缀，剥掉后取 owner/repo）。
    """
    if not isinstance(url, str) or not REPO_URL_PATTERN.match(url.strip()):
        raise AppError(
            PARAM_INVALID,
            "仓库地址不合法：必须是 https://github.com/<owner>/<repo> 形式的公开仓库",
            detail={"url": url},
        )
    match = _REPO_URL_CLEAN_RE.match(url.strip())
    if match is None:  # 理论上到不了这里（REPO_URL_PATTERN 已校验）
        raise AppError(PARAM_INVALID, "仓库地址不合法", detail={"url": url})
    return match.group(1), match.group(2)


class GitHubClient:
    """GitHub REST API 的最小封装（同步 httpx）。"""

    def __init__(
        self,
        token: str = "",
        *,
        timeout: float = HTTP_TIMEOUT,
        proxy: str | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "InterviewerAgent/0.1 (course project)",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._client = httpx.Client(
            base_url=GITHUB_API_BASE,
            headers=headers,
            timeout=timeout,
            proxy=proxy,
            transport=transport,  # 测试注入 MockTransport
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> GitHubClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # ---------------------------------------------------------------- #
    # 底层请求
    # ---------------------------------------------------------------- #
    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            response = self._client.request(method, path, **kwargs)
        except httpx.TimeoutException as exc:
            raise AppError(UPSTREAM_TIMEOUT, f"GitHub 请求超时：{path}", status=504) from exc
        except httpx.HTTPError as exc:
            raise AppError(UPSTREAM_TIMEOUT, f"GitHub 请求失败：{exc}", status=504) from exc

        if response.status_code == 404:
            raise AppError(NOT_FOUND, f"GitHub 资源不存在：{path}", status=404)
        if response.status_code in (403, 429) and self._is_rate_limited(response):
            # 403 也可能是权限问题，这里按「限流/受限」统一处理（P1 不深入区分）
            raise AppError(
                RATE_LIMIT, "GitHub 接口调用受限（可能已限流），请稍后重试", status=429
            )
        if response.status_code >= 400:
            try:
                message = response.json().get("message", response.text[:200])
            except ValueError:
                message = response.text[:200]
            raise AppError(
                RATE_LIMIT if response.status_code == 403 else 50000,
                f"GitHub 接口错误（HTTP {response.status_code}）：{message}",
                status=502,
            )
        return response

    @staticmethod
    def _is_rate_limited(response: httpx.Response) -> bool:
        remaining = response.headers.get("x-ratelimit-remaining")
        return response.status_code == 429 or remaining == "0"

    # ---------------------------------------------------------------- #
    # 对外接口（Plan §3 工具清单）
    # ---------------------------------------------------------------- #
    def get_user(self, username: str) -> dict[str, Any]:
        """用户公开信息（核验 Agent 用：比对简历中的身份/组织描述）。"""
        data = self._request("GET", f"/users/{username}").json()
        return {
            "login": data.get("login"),
            "name": data.get("name"),
            "bio": data.get("bio"),
            "company": data.get("company"),
            "blog": data.get("blog"),
            "location": data.get("location"),
            "public_repos": data.get("public_repos"),
            "followers": data.get("followers"),
            "created_at": data.get("created_at"),
            "html_url": data.get("html_url"),
        }

    def get_repo(self, owner: str, repo: str) -> dict[str, Any]:
        """仓库元数据；不存在/私有 → 42201（SRS O4）。"""
        try:
            data = self._request("GET", f"/repos/{owner}/{repo}").json()
        except AppError as exc:
            if exc.code == NOT_FOUND:
                raise AppError(
                    REPO_INACCESSIBLE,
                    f"仓库 {owner}/{repo} 不存在或不是公开仓库",
                    status=422,
                ) from exc
            raise
        return {
            "full_name": data.get("full_name"),
            "description": data.get("description"),
            "html_url": data.get("html_url"),
            "stars": data.get("stargazers_count"),
            "forks": data.get("forks_count"),
            "language": data.get("language"),
            "topics": data.get("topics") or [],
            "default_branch": data.get("default_branch") or "main",
            "pushed_at": data.get("pushed_at"),
            "size_kb": data.get("size"),
        }

    def get_languages(self, owner: str, repo: str) -> dict[str, int]:
        """各语言代码字节数 → 转百分比（保留 1 位小数，和≈100）。"""
        raw: dict[str, int] = self._request("GET", f"/repos/{owner}/{repo}/languages").json()
        total = sum(raw.values())
        if total <= 0:
            return {}
        return {lang: round(bytes_ * 100 / total, 1) for lang, bytes_ in raw.items()}

    def get_readme(self, owner: str, repo: str, *, max_bytes: int) -> str | None:
        """README 文本（大小超限取前 max_bytes 字节）；仓库没有 README 返回 None。"""
        try:
            response = self._request("GET", f"/repos/{owner}/{repo}/readme")
        except AppError as exc:
            if exc.code in (NOT_FOUND, REPO_INACCESSIBLE):
                return None
            raise
        data = response.json()
        content, size = data.get("content"), data.get("size") or 0
        if not content or data.get("encoding") != "base64" or size > max_bytes * 4:
            if size > max_bytes * 4:
                log.info("README 过大（%s 字节），跳过正文：%s/%s", size, owner, repo)
            return None
        try:
            text = base64.b64decode(content).decode("utf-8", errors="replace")
        except (binascii.Error, ValueError):
            return None
        return text

    def list_tree(self, owner: str, repo: str, ref: str, *, max_entries: int) -> list[dict[str, Any]]:
        """递归目录树 [{path, type, size}]；超出 max_entries 按路径序截断（截断会记日志）。"""
        response = self._request(
            "GET", f"/repos/{owner}/{repo}/git/trees/{ref}", params={"recursive": "1"}
        )
        data = response.json()
        entries = [
            {"path": item.get("path"), "type": item.get("type"), "size": item.get("size") or 0}
            for item in data.get("tree") or []
        ]
        truncated = bool(data.get("truncated"))
        if len(entries) > max_entries or truncated:
            log.info(
                "目录树过大（%s 条%s），截断为 %s 条：%s/%s",
                len(entries),
                "，API 标记 truncated" if truncated else "",
                max_entries,
                owner,
                repo,
            )
            entries = sorted(entries, key=lambda e: e["path"] or "")[:max_entries]
        return entries

    def read_file(
        self, owner: str, repo: str, path: str, ref: str = "", *, max_bytes: int
    ) -> str | None:
        """读文本文件内容（base64 contents API）；二进制/过大/404 → None。"""
        query = {"ref": ref} if ref else None
        try:
            response = self._request(
                "GET", f"/repos/{owner}/{repo}/contents/{path}", params=query
            )
        except AppError as exc:
            if exc.code == NOT_FOUND:
                return None
            raise
        data = response.json()
        content, encoding = data.get("content"), data.get("encoding")
        if content is None or encoding != "base64":
            return None  # 目录、超大文件（GitHub 返回数组/空 content）
        if (data.get("size") or 0) > max_bytes:
            log.info("文件过大，跳过：%s/%s %s", owner, repo, path)
            return None
        try:
            return base64.b64decode(content).decode("utf-8", errors="replace")
        except (binascii.Error, ValueError):
            return None

    def get_contributions(
        self, owner: str, repo: str, *, limit: int = 30
    ) -> dict[str, Any]:
        """最近提交摘要（核验 Agent 用：验证「独立完成 / 主导开发」类表述）。"""
        try:
            data = self._request(
                "GET", f"/repos/{owner}/{repo}/commits", params={"per_page": limit}
            ).json()
        except AppError as exc:
            if exc.code == NOT_FOUND:
                raise AppError(
                    REPO_INACCESSIBLE, f"仓库 {owner}/{repo} 不存在或不是公开仓库", status=422
                ) from exc
            raise
        commits = []
        authors: dict[str, int] = {}
        for item in data:
            author_login = (item.get("author") or {}).get("login")
            author_name = ((item.get("commit") or {}).get("author") or {}).get("name")
            key = author_login or author_name or "unknown"
            authors[key] = authors.get(key, 0) + 1
            commits.append(
                {
                    "sha": (item.get("sha") or "")[:10],
                    "author": key,
                    "date": ((item.get("commit") or {}).get("author") or {}).get("date"),
                    "message": (item.get("commit") or {}).get("message", "")[:120],
                }
            )
        return {
            "count": len(commits),
            "authors": authors,
            "last_commit_at": commits[0]["date"] if commits else None,
            "recent": commits[:10],
        }


def default_client() -> GitHubClient:
    """按 .env 配置（可选 token / 代理）构造客户端。"""
    return GitHubClient(token=settings.github_token, proxy=settings.proxy_url)
