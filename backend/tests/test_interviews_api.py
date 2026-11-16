"""interviews API（api/interviews.py）集成测试：走 TestClient 全链路。

LLM 与 GitHub 均为替身；BackgroundTasks 在 TestClient 中于响应后同步执行，
其中解析/分析两个任务线程各自开会话（SessionLocal 已被 conftest 替换到测试库）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from sqlalchemy import select

from app.models.orm import Interview, Message
from tests.conftest import register_and_login

REPO_URL = "https://github.com/octocat/Hello-World"

_PROFILE_JSON = """
{
  "basic": {"name": "张三", "school": "某大学", "major": "计算机", "degree": "本科", "grad_date": "2027-06"},
  "skills": ["Python", "MySQL"],
  "projects": [
    {"id": "P1", "name": "订单系统", "role": "后端", "period": "2026",
     "tech_stack": ["FastAPI", "Redis"], "description": "订单模块", "repo_url": "REPO"}
  ],
  "awards": [{"name": "蓝桥杯", "level": "省级", "date": "2025"}],
  "internships": []
}
""".replace("REPO", REPO_URL)

_KEY_FILES_JSON = '{"files": ["main.py"]}'
_REPORT_JSON = """
{
  "readme_summary": "示例仓库。",
  "tech_stack": ["Python"],
  "key_modules": [{"name": "main", "path": "main.py", "summary": "入口"}],
  "findings": [{"text": "main.py 实现了入口逻辑", "source_url": "REPO/blob/main/main.py"}],
  "related_project_id": "P1"
}
""".replace("REPO/blob", f"{REPO_URL}/blob")


class StubGitHub:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail

    def __enter__(self):
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def get_repo(self, owner: str, repo: str) -> dict[str, Any]:
        if self.fail:
            from app.core.errors import AppError

            raise AppError(42201, f"仓库 {owner}/{repo} 不存在或不是公开仓库", status=422)
        return {
            "full_name": f"{owner}/{repo}", "description": "demo",
            "html_url": REPO_URL, "stars": 42, "forks": 7, "language": "Python",
            "topics": [], "default_branch": "main",
            "pushed_at": "2026-03-01T00:00:00Z", "size_kb": 10,
        }

    def get_languages(self, owner: str, repo: str) -> dict[str, float]:
        return {"Python": 100.0}

    def get_readme(self, owner: str, repo: str, *, max_bytes: int) -> str | None:
        return "# demo"

    def list_tree(self, owner: str, repo: str, ref: str, *, max_entries: int) -> list[dict[str, Any]]:
        return [
            {"path": "README.md", "type": "blob", "size": 5},
            {"path": "main.py", "type": "blob", "size": 100},
        ]

    def read_file(self, owner: str, repo: str, path: str, ref: str = "", *, max_bytes: int) -> str | None:
        return "print('hi')"


def _stub_llm() -> GenericFakeChatModel:
    return GenericFakeChatModel(
        messages=iter([AIMessage(content=_PROFILE_JSON)])
    )


def _stub_llm_analysis() -> GenericFakeChatModel:
    return GenericFakeChatModel(messages=iter([AIMessage(content=_KEY_FILES_JSON), AIMessage(content=_REPORT_JSON)]))


@pytest.fixture()
def patched_backend(monkeypatch, tmp_path: Path):
    """替换 LLM / GitHub / 落盘，返回收集器（记录写盘的文件名）。"""
    saved: list[Path] = []

    def fake_save(data: bytes, ext: str) -> str:
        path = tmp_path / f"resume-{len(saved)}{ext}"
        path.write_bytes(data)
        saved.append(path)
        return f"uploads/{path.name}"

    def fake_resolve(relative: str | None):
        if not relative:
            return None
        candidate = tmp_path / Path(relative).name
        return candidate if candidate.exists() else None

    monkeypatch.setattr("app.services.storage.save_resume", fake_save)
    monkeypatch.setattr("app.services.storage.resolve_resume_path", fake_resolve)
    monkeypatch.setattr("app.services.resume_parser.get_llm", lambda *a, **k: _stub_llm())
    monkeypatch.setattr(
        "app.services.project_analysis.get_llm", lambda *a, **k: _stub_llm_analysis()
    )
    monkeypatch.setattr(
        "app.services.project_analysis.default_client", lambda: StubGitHub()
    )
    return saved


def _sync(db) -> None:
    """结束当前读事务快照并失效缓存实例。

    MySQL 默认 REPEATABLE READ：本测试复用同一个会话，POST 请求里最后一次
    SELECT 会开启一个一直挂着的读事务，后台任务线程的提交在该快照里不可见
    （生产环境每个请求都是新会话，无此问题）。rollback 即开新快照。
    """
    db.rollback()


def _create(client, headers, *, filename="resume.txt", content="张三的简历内容" * 5,
            repos: list[str] | None = None) -> Any:
    repos = repos if repos is not None else [REPO_URL]
    return client.post(
        "/api/v1/interviews",
        files={"resume": (filename, content.encode("utf-8"), "application/octet-stream")},
        data={"repo_urls": repos},
        headers=headers,
    )


def test_create_full_pipeline_to_awaiting_confirm(client, db, patched_backend):
    headers = register_and_login(client)
    response = _create(client, headers)
    assert response.status_code == 202, response.text
    interview_id = response.json()["id"]
    assert response.json()["status"] == "preparing"
    assert patched_backend and patched_backend[0].name.startswith("resume-")  # uuid 落盘，非原名

    # BackgroundTasks 已在响应后同步跑完 → 两任务完成，状态推进
    _sync(db)
    interview = db.get(Interview, interview_id)
    assert interview.parse_status == "done"
    assert interview.analysis_status == "done"
    assert interview.status == "awaiting_confirm"
    assert interview.profile["basic"]["name"] == "张三"
    assert interview.project_reports[0]["repo"]["stars"] == 42
    assert interview.project_reports[0]["findings"][0]["id"] == "F-001"


def test_create_validation_errors(client, db, patched_backend):
    headers = register_and_login(client, "validator")

    # 格式不支持 → 40001
    response = _create(client, headers, filename="resume.doc")
    assert response.status_code == 400
    assert response.json()["code"] == 40001

    # 超过 10MB → 40002
    response = _create(client, headers, content="x" * (10 * 1024 * 1024 + 1))
    assert response.status_code == 400
    assert response.json()["code"] == 40002

    # 空文件 → 40001
    response = _create(client, headers, content="")
    assert response.status_code == 400

    # 仓库数量 0 → 40000
    response = _create(client, headers, repos=[])
    assert response.status_code == 400
    assert response.json()["code"] == 40000

    # 仓库地址不合法 → 40000
    response = _create(client, headers, repos=["https://gitee.com/a/b"])
    assert response.status_code == 400
    assert response.json()["code"] == 40000

    # 仓库数量超上限 → 40000
    response = _create(client, headers, repos=[REPO_URL] * 4)
    assert response.status_code == 400


def test_get_detail_and_list(client, db, patched_backend):
    headers = register_and_login(client, "reader")
    interview_id = _create(client, headers).json()["id"]
    _sync(db)  # 让复用会话丢弃创建请求时的旧实例（后台任务已提交新状态）

    detail = client.get(f"/api/v1/interviews/{interview_id}", headers=headers).json()
    assert detail["id"] == interview_id
    assert detail["status"] == "awaiting_confirm"
    assert detail["profile"]["basic"]["name"] == "张三"
    assert detail["project_reports"][0]["findings"][0]["source_url"].startswith(REPO_URL)
    assert detail["config"] is None  # 尚未确认

    listing = client.get("/api/v1/interviews", headers=headers).json()
    assert len(listing) == 1
    assert listing[0]["id"] == interview_id
    assert listing[0]["status"] == "awaiting_confirm"


def test_confirm_profile_saves_and_moves_to_initializing(client, db, patched_backend):
    headers = register_and_login(client, "confirmer")
    interview_id = _create(client, headers).json()["id"]
    _sync(db)

    body = {
        "profile": {
            "basic": {"name": "张三丰", "school": "新大学", "major": "软件工程",
                      "degree": "硕士", "grad_date": "2028"},
            "skills": ["Python", "Rust"],
            "projects": [
                {"id": "P1", "name": "订单系统 v2", "role": "后端", "period": "2026",
                 "tech_stack": ["FastAPI"], "description": "改过", "repo_url": REPO_URL},
                {"id": "P9", "name": "新项目", "role": None, "period": None,
                 "tech_stack": ["Vue"], "description": None, "repo_url": None},
            ],
            "awards": [], "internships": [],
        },
        "config": {"style": "犀利", "difficulty": 3, "position": "算法"},
    }
    response = client.put(f"/api/v1/interviews/{interview_id}/profile", json=body, headers=headers)
    assert response.status_code == 200, response.text
    detail = response.json()
    assert detail["status"] == "initializing"
    assert detail["config"]["difficulty"] == 3
    # 项目 id 重新归一化
    assert [p["id"] for p in detail["profile"]["projects"]] == ["P1", "P2"]

    _sync(db)
    interview = db.get(Interview, interview_id)
    assert interview.profile["projects"][0]["name"] == "订单系统 v2"
    assert interview.config == {"style": "犀利", "difficulty": 3, "position": "算法"}

    # 重复确认 → 42202
    response = client.put(f"/api/v1/interviews/{interview_id}/profile", json=body, headers=headers)
    assert response.status_code == 422
    assert response.json()["code"] == 42202


def test_confirm_before_ready_rejected(client, db, patched_backend):
    headers = register_and_login(client, "earlybird")
    body = {"profile": {"basic": {}, "skills": [], "projects": [], "awards": [], "internships": []},
            "config": {"style": "严谨", "difficulty": 2, "position": "后端"}}
    response = client.post("/api/v1/interviews",
                           files={"resume": ("r.txt", b"resume", "application/octet-stream")},
                           data={"repo_urls": [REPO_URL]}, headers=headers)
    interview_id = response.json()["id"]
    # 任务已完成推进到 awaiting_confirm 之前（status=preparing）时不可确认 ——
    # 这里直接用尚未确认的状态：先造一个 failed 的分析任务场景
    _sync(db)
    interview = db.get(Interview, interview_id)
    interview.analysis_status = "failed"
    interview.status = "preparing"
    db.commit()

    response = client.put(f"/api/v1/interviews/{interview_id}/profile", json=body, headers=headers)
    assert response.status_code == 422
    assert response.json()["code"] == 42202


def test_retry_parse_recovers_from_failure(client, db, patched_backend, monkeypatch):
    headers = register_and_login(client, "retrier")

    # 先让简历解析失败（LLM 输出不可解析）
    bad_llm = GenericFakeChatModel(messages=iter([AIMessage(content="坏"), AIMessage(content="还是坏")]))
    monkeypatch.setattr("app.services.resume_parser.get_llm", lambda *a, **k: bad_llm)
    interview_id = _create(client, headers).json()["id"]

    _sync(db)
    interview = db.get(Interview, interview_id)
    assert interview.parse_status == "failed"
    assert interview.warnings[0]["task"] == "parse"
    assert interview.status == "preparing"  # 未推进

    # 不允许重试未失败的任务 / 未知任务
    response = client.post(f"/api/v1/interviews/{interview_id}/retry/analysis", headers=headers)
    assert response.status_code == 422 and response.json()["code"] == 42202
    response = client.post(f"/api/v1/interviews/{interview_id}/retry/bogus", headers=headers)
    assert response.status_code == 400

    # 换回正常 LLM 后重试 parse → 成功并推进 awaiting_confirm
    monkeypatch.setattr("app.services.resume_parser.get_llm", lambda *a, **k: _stub_llm())
    response = client.post(f"/api/v1/interviews/{interview_id}/retry/parse", headers=headers)
    assert response.status_code == 202

    _sync(db)
    interview = db.get(Interview, interview_id)
    assert interview.parse_status == "done"
    assert interview.status == "awaiting_confirm"


def test_delete_cascades_and_removes_file(client, db, patched_backend, monkeypatch, tmp_path):
    headers = register_and_login(client, "deleter")
    interview_id = _create(client, headers).json()["id"]

    _sync(db)
    interview = db.get(Interview, interview_id)
    db.add(Message(interview_id=interview_id, seq=1, role="interviewer", content="你好"))
    db.commit()

    removed: list[str] = []
    monkeypatch.setattr("app.services.storage.remove_resume", lambda rel: removed.append(rel) or True)

    response = client.delete(f"/api/v1/interviews/{interview_id}", headers=headers)
    assert response.status_code == 204
    _sync(db)
    assert db.get(Interview, interview_id) is None
    assert db.scalars(select(Message).where(Message.interview_id == interview_id)).all() == []
    assert removed and removed[0].startswith("uploads/")


def test_access_other_users_interview_forbidden(client, db, patched_backend):
    owner_headers = register_and_login(client, "owner01")
    other_headers = register_and_login(client, "other01")
    interview_id = _create(client, owner_headers).json()["id"]

    response = client.get(f"/api/v1/interviews/{interview_id}", headers=other_headers)
    assert response.status_code == 403
    assert response.json()["code"] == 40301

    response = client.delete(f"/api/v1/interviews/{interview_id}", headers=other_headers)
    assert response.status_code == 403


def test_end_to_end_flow_pages_data(client, db, patched_backend):
    """P1 完成标志链路：上传 → 画像 + 项目分析 → 编辑确认。"""
    headers = register_and_login(client, "flow01")
    created = _create(client, headers).json()
    interview_id = created["id"]
    _sync(db)

    detail = client.get(f"/api/v1/interviews/{interview_id}", headers=headers).json()
    assert detail["parse_status"] == "done" and detail["analysis_status"] == "done"

    # 用户在画像里修正了姓名并确认
    profile = detail["profile"]
    profile["basic"]["name"] = "张三（已核对）"
    body = {"profile": profile, "config": {"style": "温和", "difficulty": 1, "position": "前端"}}
    response = client.put(f"/api/v1/interviews/{interview_id}/profile", json=body, headers=headers)
    assert response.status_code == 200
    detail = response.json()
    assert detail["status"] == "initializing"
    assert detail["profile"]["basic"]["name"] == "张三（已核对）"
    # P1：init/research 尚未实现，保持 pending（P2 派发后变 running→done）
    assert detail["init_status"] == "pending"
    assert detail["research_status"] == "pending"
