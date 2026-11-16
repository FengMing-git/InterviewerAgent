"""项目分析（services/project_analysis.py）测试：stub GitHub + 假 LLM。

覆盖：单仓库流水线组装、findings 白名单裁剪、key_modules 路径校验、
related_project_id 校验、仓库 404 → 42201、run_analysis 状态汇总（done/partial/failed）。
"""

from __future__ import annotations

import base64
from typing import Any

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from app.core.errors import AppError
from app.models.orm import Interview, User
from app.services.project_analysis import analyze_repo, run_analysis

REPO_URL = "https://github.com/octocat/Hello-World"
BLOB = f"{REPO_URL}/blob/main/"

_PROFILE = {
    "basic": {"name": "张三"},
    "skills": ["Python"],
    "projects": [
        {"id": "P1", "name": "Hello World 项目", "role": "作者", "period": "2026",
         "tech_stack": ["Python"], "description": "demo", "repo_url": REPO_URL},
        {"id": "P2", "name": "另一个项目", "role": None, "period": None,
         "tech_stack": [], "description": None, "repo_url": None},
    ],
    "awards": [],
    "internships": [],
}


class StubGitHub:
    """按 analyze_repo 调用面造的最小替身。"""

    def __init__(self, *, repo_404: bool = False) -> None:
        self.repo_404 = repo_404
        self.read_calls: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def get_repo(self, owner: str, repo: str) -> dict[str, Any]:
        if self.repo_404:
            raise AppError(42201, f"仓库 {owner}/{repo} 不存在或不是公开仓库", status=422)
        return {
            "full_name": f"{owner}/{repo}", "description": "demo repo",
            "html_url": REPO_URL, "stars": 42, "forks": 7, "language": "Python",
            "topics": ["demo"], "default_branch": "main",
            "pushed_at": "2026-03-01T00:00:00Z", "size_kb": 100,
        }

    def get_languages(self, owner: str, repo: str) -> dict[str, int]:
        return {"Python": 90.0, "Shell": 10.0}

    def get_readme(self, owner: str, repo: str, *, max_bytes: int) -> str | None:
        return "# Hello World\n示例项目"

    def list_tree(self, owner: str, repo: str, ref: str, *, max_entries: int) -> list[dict[str, Any]]:
        return [
            {"path": "README.md", "type": "blob", "size": 30},
            {"path": "main.py", "type": "blob", "size": 100},
            {"path": "src/core.py", "type": "blob", "size": 200},
            {"path": "docs", "type": "tree", "size": 0},
        ]

    def read_file(self, owner: str, repo: str, path: str, ref: str = "", *, max_bytes: int) -> str | None:
        self.read_calls.append(path)
        return f"# {path} 的内容：实现了订单缓存模块"


_KEY_FILES_JSON = '{"files": ["main.py", "src/core.py", "README.md", "不存在.py"]}'
_REPORT_JSON = """
{
  "readme_summary": "这是一个示例仓库。",
  "tech_stack": ["Python"],
  "key_modules": [
    {"name": "core", "path": "src/core.py", "summary": "核心逻辑"},
    {"name": "幻觉模块", "path": "src/hallucinated.py", "summary": "路径不存在"},
    {"name": "main", "path": null, "summary": "入口"}
  ],
  "findings": [
    {"text": "src/core.py 实现了订单缓存模块", "source_url": "REPO/blob/main/src/core.py"},
    {"text": "main.py 是入口", "source_url": "REPO/blob/main/main.py"},
    {"text": "编造的来源", "source_url": "https://evil.example.com/xxx"},
    {"text": "仓库 stars 数为 42", "source_url": "REPO"}
  ],
  "related_project_id": "P1"
}
""".replace("REPO/blob", f"{REPO_URL}/blob").replace('"source_url": "REPO"', f'"source_url": "{REPO_URL}"')


def _fake_llm(*contents: str) -> GenericFakeChatModel:
    return GenericFakeChatModel(messages=iter([AIMessage(content=c) for c in contents]))


def test_analyze_repo_assembles_report():
    llm = _fake_llm(_KEY_FILES_JSON, _REPORT_JSON)
    stub = StubGitHub()

    analysis = analyze_repo(llm, stub, REPO_URL, _PROFILE)

    report = analysis.report
    assert report.repo.url == REPO_URL
    assert report.repo.stars == 42
    assert report.repo.language_ratio == {"Python": 90.0, "Shell": 10.0}
    assert report.repo.last_commit_at == "2026-03-01T00:00:00Z"
    assert report.readme_summary == "这是一个示例仓库。"

    # 关键文件：LLM 选了 4 个（含一个不存在的），不存在.py 被过滤；README 被 LLM
    # 选中时照读（提示词只是建议不选，不硬拦）
    assert stub.read_calls == ["main.py", "src/core.py", "README.md"]

    # key_modules：路径不存在的被丢弃
    assert [m.path for m in report.key_modules if m.path] == ["src/core.py"]
    assert len(report.key_modules) == 2

    # findings：白名单外的 URL 被丢弃（3 条保留）
    urls = {f.source_url for f in report.findings}
    assert "https://evil.example.com/xxx" not in urls
    assert f"{REPO_URL}/blob/main/src/core.py" in urls
    assert REPO_URL in urls

    assert report.related_project_id == "P1"


def test_analyze_repo_related_project_id_validated():
    report_json = _REPORT_JSON.replace('"related_project_id": "P1"', '"related_project_id": "P9"')
    llm = _fake_llm(_KEY_FILES_JSON, report_json)
    analysis = analyze_repo(llm, StubGitHub(), REPO_URL, _PROFILE)
    assert analysis.report.related_project_id is None  # 不在画像项目集合 → 置空


def test_analyze_repo_repo_not_found_42201():
    llm = _fake_llm()
    with pytest.raises(AppError) as exc_info:
        analyze_repo(llm, StubGitHub(repo_404=True), REPO_URL, None)
    assert exc_info.value.code == 42201


def test_analyze_repo_key_file_fallback_heuristic():
    # LLM 两次都挑不出文件（structured_generate 重试一次后仍失败）→ 启发式兜底
    llm = _fake_llm("我不会选", "还是不会", _REPORT_JSON)
    stub = StubGitHub()
    analysis = analyze_repo(llm, stub, REPO_URL, None)
    assert stub.read_calls  # 仍读到了文件
    assert analysis.notes  # 记了「走启发式」提示


# --------------------------------------------------------------------------- #
# run_analysis 任务闭环
# --------------------------------------------------------------------------- #
def _setup_interview(db, repo_urls: list[str]) -> int:
    from sqlalchemy import select

    from app.core.security import hash_password

    user = User(username=f"an{db.scalar(select(User.id).order_by(User.id.desc())) or 0:04d}",
                password_hash=hash_password("password123"))
    db.add(user)
    db.commit()
    interview = Interview(user_id=user.id, repo_urls=repo_urls, profile=_PROFILE)
    db.add(interview)
    db.commit()
    return interview.id


def test_run_analysis_all_ok(db, monkeypatch):
    interview_id = _setup_interview(db, [REPO_URL])
    llm = _fake_llm(_KEY_FILES_JSON, _REPORT_JSON)
    monkeypatch.setattr("app.services.project_analysis.get_llm", lambda *a, **k: llm)
    monkeypatch.setattr("app.services.project_analysis.default_client", lambda: StubGitHub())

    run_analysis(interview_id)

    db.expire_all()
    interview = db.get(Interview, interview_id)
    assert interview.analysis_status == "done"
    reports = interview.project_reports
    assert len(reports) == 1
    assert reports[0]["findings"][0]["id"] == "F-001"  # 统一编号


def test_run_analysis_one_repo_404_marks_partial(db, monkeypatch):
    interview_id = _setup_interview(db, [REPO_URL, "https://github.com/octocat/Nope"])
    llm = _fake_llm(_KEY_FILES_JSON, _REPORT_JSON)
    monkeypatch.setattr("app.services.project_analysis.get_llm", lambda *a, **k: llm)

    class Mixed(StubGitHub):
        def __init__(self):
            super().__init__()

        def get_repo(self, owner: str, repo: str) -> dict[str, Any]:
            if repo == "Nope":
                raise AppError(42201, "仓库不存在", status=422)
            return super().get_repo(owner, repo)

    monkeypatch.setattr("app.services.project_analysis.default_client", lambda: Mixed())

    run_analysis(interview_id)

    db.expire_all()
    interview = db.get(Interview, interview_id)
    assert interview.analysis_status == "partial"
    assert len(interview.project_reports) == 1
    assert any(w.get("code") == 42201 for w in interview.warnings)


def test_run_analysis_all_fail(db, monkeypatch):
    interview_id = _setup_interview(db, [REPO_URL])
    monkeypatch.setattr("app.services.project_analysis.get_llm", lambda *a, **k: _fake_llm())
    monkeypatch.setattr(
        "app.services.project_analysis.default_client", lambda: StubGitHub(repo_404=True)
    )

    run_analysis(interview_id)

    db.expire_all()
    interview = db.get(Interview, interview_id)
    assert interview.analysis_status == "failed"
    assert interview.project_reports is None
    assert interview.warnings and interview.warnings[0]["code"] == 42201
