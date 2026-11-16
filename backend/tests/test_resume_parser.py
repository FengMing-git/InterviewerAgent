"""简历解析（services/resume_parser.py）测试：四格式抽取 + 40003 + LLM 画像 + 任务闭环。"""

from __future__ import annotations

from pathlib import Path

import fitz
import pytest
from docx import Document as DocxDocument
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from app.core.errors import AppError
from app.models.orm import Interview, User
from app.models.schemas import ResumeProfile
from app.services.resume_parser import extract_text, parse_resume, run_parse

# --------------------------------------------------------------------------- #
# 文本抽取（无数据库依赖）
# --------------------------------------------------------------------------- #
def test_extract_txt_utf8(tmp_path: Path):
    path = tmp_path / "resume.txt"
    path.write_text("张三 后端开发 熟悉 Python、FastAPI", encoding="utf-8")
    assert "张三" in extract_text(path)


def test_extract_txt_gbk_fallback(tmp_path: Path):
    path = tmp_path / "resume_gbk.txt"
    path.write_bytes("张三 简历".encode("gbk"))
    assert "张三" in extract_text(path)


def test_extract_md(tmp_path: Path):
    path = tmp_path / "resume.md"
    path.write_text("# 李四\n- Go / Redis", encoding="utf-8")
    assert "李四" in extract_text(path)


def test_extract_docx_including_tables(tmp_path: Path):
    doc = DocxDocument()
    doc.add_paragraph("王五 2027 届")
    table = doc.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text = "技能"
    table.rows[0].cells[1].text = "Java、Spring"
    path = tmp_path / "resume.docx"
    doc.save(path)
    text = extract_text(path)
    assert "王五" in text and "Spring" in text


def test_extract_pdf_with_text(tmp_path: Path):
    with fitz.open() as pdf:
        page = pdf.new_page()
        page.insert_text((72, 72), "Zhao Liu Python developer resume")
        pdf.save(tmp_path / "resume.pdf")
    assert "Zhao Liu" in extract_text(tmp_path / "resume.pdf")


def test_extract_pdf_without_text_layer(tmp_path: Path):
    # 空白页 PDF：没有文字层 → 40003（SRS O4）
    with fitz.open() as pdf:
        pdf.new_page()
        pdf.save(tmp_path / "scan.pdf")
    with pytest.raises(AppError) as exc_info:
        extract_text(tmp_path / "scan.pdf")
    assert exc_info.value.code == 40003
    assert "扫描" in exc_info.value.message


def test_extract_unsupported_extension(tmp_path: Path):
    path = tmp_path / "resume.doc"
    path.write_bytes(b"\xd0\xcf\x11\xe0")
    with pytest.raises(AppError) as exc_info:
        extract_text(path)
    assert exc_info.value.code == 40001


def test_extract_empty_plain_file(tmp_path: Path):
    path = tmp_path / "empty.txt"
    path.write_text("   ", encoding="utf-8")
    with pytest.raises(AppError) as exc_info:
        extract_text(path)
    assert exc_info.value.code == 40003


# --------------------------------------------------------------------------- #
# LLM 画像（假模型）
# --------------------------------------------------------------------------- #
_VALID_PROFILE_JSON = """
{
  "basic": {"name": "张三", "school": "某大学", "major": "计算机", "degree": "本科", "grad_date": "2027-06"},
  "skills": ["Python", "FastAPI", "MySQL"],
  "projects": [
    {"id": "P?", "name": "订单系统", "role": "后端负责人", "period": "2026.03-2026.09",
     "tech_stack": ["FastAPI", "Redis"], "description": "高并发订单模块", "repo_url": "https://github.com/u/order"},
    {"name": "博客", "role": null, "period": null, "tech_stack": [" ", "Vue", "Vue"], "description": null, "repo_url": null}
  ],
  "awards": [{"name": "蓝桥杯", "level": "省级", "date": "2025"}],
  "internships": [{"company": "某厂", "position": "后端实习", "period": "2026 夏", "description": " CRUD 开发"}]
}
"""


def test_parse_resume_normalizes_ids_and_dedupes():
    llm = GenericFakeChatModel(messages=iter([AIMessage(content=_VALID_PROFILE_JSON)]))
    profile, result = parse_resume(llm, "简历原文……")
    assert [p.id for p in profile.projects] == ["P1", "P2"]  # id 由代码统一重排
    assert profile.projects[1].tech_stack == ["Vue"]  # 空串与重复项被清理
    assert profile.basic.grad_date == "2027-06"
    assert result.parsed is not None


def test_parse_resume_llm_failure_raises():
    llm = GenericFakeChatModel(messages=iter([AIMessage(content="我不会"), AIMessage(content="still not json")]))
    with pytest.raises(AppError):
        parse_resume(llm, "简历原文……")


# --------------------------------------------------------------------------- #
# 后台任务闭环（数据库）
# --------------------------------------------------------------------------- #
def _make_user_and_interview(db, tmp_path: Path, filename: str) -> int:
    from app.core.security import hash_password

    user = User(username="parser01", password_hash=hash_password("password123"))
    db.add(user)
    db.commit()
    interview = Interview(
        user_id=user.id,
        resume_path=f"uploads/{filename}",
        repo_urls=["https://github.com/octocat/Hello-World"],
    )
    db.add(interview)
    db.commit()
    return interview.id


def test_run_parse_happy_path(db, monkeypatch, tmp_path: Path):
    interview_id = _make_user_and_interview(db, tmp_path, "a.txt")

    # 原件指向 tmp（绕开真实 uploads/ 目录）
    resume_file = tmp_path / "a.txt"
    resume_file.write_text("张三，Python 后端，做过订单系统", encoding="utf-8")
    monkeypatch.setattr("app.services.storage.resolve_resume_path", lambda rel: resume_file)

    llm = GenericFakeChatModel(messages=iter([AIMessage(content=_VALID_PROFILE_JSON)]))
    monkeypatch.setattr("app.services.resume_parser.get_llm", lambda *a, **k: llm)

    run_parse(interview_id)

    db.expire_all()
    interview = db.get(Interview, interview_id)
    assert interview.parse_status == "done"
    assert interview.profile["basic"]["name"] == "张三"
    assert [p["id"] for p in interview.profile["projects"]] == ["P1", "P2"]


def test_run_parse_failure_sets_failed_and_warning(db, monkeypatch, tmp_path: Path):
    interview_id = _make_user_and_interview(db, tmp_path, "b.txt")
    resume_file = tmp_path / "b.txt"
    resume_file.write_text("内容", encoding="utf-8")
    monkeypatch.setattr("app.services.storage.resolve_resume_path", lambda rel: resume_file)

    llm = GenericFakeChatModel(messages=iter([AIMessage(content="坏输出"), AIMessage(content="还是坏")]))
    monkeypatch.setattr("app.services.resume_parser.get_llm", lambda *a, **k: llm)

    run_parse(interview_id)

    db.expire_all()
    interview = db.get(Interview, interview_id)
    assert interview.parse_status == "failed"
    assert interview.warnings and interview.warnings[0]["task"] == "parse"
    assert interview.warnings[0]["code"] == 50000


def test_run_parse_pdf_scan_returns_40003_warning(db, monkeypatch, tmp_path: Path):
    interview_id = _make_user_and_interview(db, tmp_path, "scan.pdf")
    scan_file = tmp_path / "scan.pdf"
    with fitz.open() as pdf:
        pdf.new_page()
        pdf.save(scan_file)
    monkeypatch.setattr("app.services.storage.resolve_resume_path", lambda rel: scan_file)
    llm = GenericFakeChatModel(messages=iter([]))
    monkeypatch.setattr("app.services.resume_parser.get_llm", lambda *a, **k: llm)

    run_parse(interview_id)

    db.expire_all()
    interview = db.get(Interview, interview_id)
    assert interview.parse_status == "failed"
    assert interview.warnings[0]["code"] == 40003
