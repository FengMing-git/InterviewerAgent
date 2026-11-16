"""简历解析服务（Plan §6.1 ①；SRS 功能模块 2 / 模块 M4）。

流水线：抽文本（PyMuPDF / python-docx / 直读）→ 截断 → LLM 结构化输出 D1
（Pydantic 校验 + 失败带错重试一次）→ 项目 id 重排 P1..Pn → 入库。

错误码（SRS O4）：40001 格式不支持、40002 超过 10 MB（API 层校验）、
40003 PDF 无文字层。任务失败不抛出接口（后台执行），落成 parse_status=failed
+ warning，前端可重试。
"""

from __future__ import annotations

from pathlib import Path

import fitz  # PyMuPDF
from docx import Document as DocxDocument

from app.agent.llm import get_llm, structured_generate
from app.agent.prompts import RESUME_PARSE_SYSTEM, resume_parse_user_prompt
from app.config import RESUME_TEXT_MAX_CHARS
from app.core.errors import RESUME_FORMAT_UNSUPPORTED, RESUME_NO_TEXT, AppError
from app.core.logger import get_logger
from app.models.orm import Interview
from app.models.schemas import ResumeProfile, normalize_profile
from app.services.tasks import add_warning, load_user, merge_token_usage, task_session

log = get_logger(__name__)

# PDF 抽出文本低于该字符数视为扫描件（无文字层）
_MIN_PDF_TEXT_CHARS = 20


def extract_text(path: Path) -> str:
    """按扩展名抽取简历文本。失败抛 AppError（40001 / 40003）。"""
    ext = path.suffix.lower()
    if ext == ".pdf":
        text = _extract_pdf(path)
        if len(text.strip()) < _MIN_PDF_TEXT_CHARS:
            raise AppError(
                RESUME_NO_TEXT,
                "PDF 中未检测到文字层（可能是扫描件或图片版），请换用文字版简历",
                status=400,
            )
        return text
    if ext == ".docx":
        text = _extract_docx(path)
    elif ext in (".txt", ".md"):
        text = _extract_plain(path)
    else:
        raise AppError(RESUME_FORMAT_UNSUPPORTED, f"不支持的简历格式：{ext}", status=400)
    if not text.strip():
        raise AppError(RESUME_NO_TEXT, "简历内容为空，无法抽取文本", status=400)
    return text


def _extract_pdf(path: Path) -> str:
    try:
        with fitz.open(path) as doc:
            return "\n".join(page.get_text() for page in doc)
    except Exception as exc:  # noqa: BLE001 —— 损坏的 PDF 统一按无文字层处理
        raise AppError(
            RESUME_NO_TEXT, "PDF 文件无法读取，请确认文件未损坏", status=400
        ) from exc


def _extract_docx(path: Path) -> str:
    try:
        doc = DocxDocument(path)
    except Exception as exc:  # noqa: BLE001
        raise AppError(
            RESUME_NO_TEXT, "DOCX 文件无法读取，请确认文件未损坏", status=400
        ) from exc
    parts = [paragraph.text for paragraph in doc.paragraphs]
    for table in doc.tables:
        for row in table.rows:
            parts.append(" | ".join(cell.text.strip() for cell in row.cells))
    return "\n".join(parts)


def _extract_plain(path: Path) -> str:
    raw = path.read_bytes()
    # 中文简历 TXT 常见 GBK 编码：UTF-8 解不开时退回 GBK（替换无法解码的字符）
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("gbk", errors="replace")


def parse_resume(llm: object, resume_text: str) -> tuple[ResumeProfile, StructuredOutput]:
    """调用 LLM 生成 D1 画像（供服务与测试复用）。"""
    truncated = resume_text[:RESUME_TEXT_MAX_CHARS]
    if len(resume_text) > RESUME_TEXT_MAX_CHARS:
        log.info("简历文本超长，截断至 %d 字符", RESUME_TEXT_MAX_CHARS)
    result = structured_generate(
        llm,
        RESUME_PARSE_SYSTEM,
        resume_parse_user_prompt(truncated),
        ResumeProfile,
        purpose="简历画像解析",
    )
    return normalize_profile(result.parsed), result


# --------------------------------------------------------------------------- #
# 后台任务入口
# --------------------------------------------------------------------------- #
def run_parse(interview_id: int) -> None:
    """POST /interviews / retry(parse) 的后台任务。"""
    with task_session() as db:
        interview = db.get(Interview, interview_id)
        if interview is None or not interview.resume_path:
            log.warning("简历解析任务丢弃：interview_id=%s 无原件", interview_id)
            return
        from app.services.storage import resolve_resume_path

        path = resolve_resume_path(interview.resume_path)

        interview.parse_status = "running"
        db.commit()
        try:
            if path is None or not path.is_file():
                raise AppError(
                    RESUME_NO_TEXT, "简历原件已不存在，请重新上传", status=400
                )
            text = extract_text(path)
            user = load_user(db, interview.user_id)
            llm = get_llm(user, purpose="parse")
            profile, result = parse_resume(llm, text)

            interview.profile = profile.model_dump()
            interview.parse_status = "done"
            db.commit()
            merge_token_usage(interview_id, result.input_tokens, result.output_tokens)
            log.info("简历解析完成：interview_id=%s（原文 %d 字符）", interview_id, len(text))
        except AppError as exc:
            _fail_parse(db, interview_id, exc.code, exc.message)
        except Exception as exc:  # noqa: BLE001 —— 后台任务兜底，不能让线程带栈崩溃
            log.exception("简历解析任务异常：interview_id=%s", interview_id)
            _fail_parse(db, interview_id, None, f"简历解析异常：{exc}")


def _fail_parse(db, interview_id: int, code: int | None, message: str) -> None:
    db.rollback()  # 丢弃未提交的中间状态（如 profile 半写）
    interview = db.get(Interview, interview_id)
    if interview is not None:
        interview.parse_status = "failed"
        db.commit()
    add_warning(db, interview_id, "parse", message, code)
