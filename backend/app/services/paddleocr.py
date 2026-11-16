"""PaddleOCR-VL 云端 OCR：后端直接调用 MCP，不经过 LLM 工具选择。

同步入口供后台任务线程使用；每次请求独立 session，退出时关闭子进程。
只上传前 OCR_MAX_PAGES 页；不记录简历正文、token 或 MCP 原始错误。
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import tempfile
from pathlib import Path

import pymupdf
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from app.config import OCR_MAX_PAGES, OCR_TIMEOUT, PADDLEOCR_MODEL, settings
from app.core.errors import AppError, INTERNAL, RESUME_NO_TEXT, UPSTREAM_TIMEOUT
from app.core.logger import get_logger

log = get_logger(__name__)


def _clean_text(text: str) -> str:
    # 只去掉图片与布局 div，保留 Markdown 标题、表格和链接。
    text = re.sub(r"<img\b[^>]*>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"</?div\b[^>]*>", "", text, flags=re.IGNORECASE)
    return re.sub(r"\n[ \t]*\n(?:[ \t]*\n)+", "\n\n", text).strip()


def _result_text(result: object) -> str:
    if getattr(result, "is_error", False):
        raise AppError(INTERNAL, "PaddleOCR-VL 识别失败，请稍后重试或检查服务凭证", status=502)
    content = getattr(result, "content", None)
    if content is None:
        raise AppError(INTERNAL, "PaddleOCR-VL 返回了无法识别的响应", status=502)
    text = "\n".join(
        item.text for item in content if getattr(item, "type", None) == "text"
    )
    return _clean_text(text)


def _leaves(exc: Exception) -> list[Exception]:
    if isinstance(exc, ExceptionGroup):
        return [leaf for child in exc.exceptions for leaf in _leaves(child)]
    return [exc]


async def _recognize(path: Path, command: str, token: str) -> str:
    env = {
        **os.environ,
        "PADDLEOCR_MCP_MODEL": PADDLEOCR_MODEL,
        "PADDLEOCR_MCP_PPOCR_SOURCE": "aistudio",
        "PADDLEOCR_MCP_AISTUDIO_ACCESS_TOKEN": token,
        "FASTMCP_SHOW_CLI_BANNER": "false",
        "PYTHONIOENCODING": "utf-8",
    }
    if settings.proxy_url:
        env.update(HTTP_PROXY=settings.proxy_url, HTTPS_PROXY=settings.proxy_url)
    params = StdioServerParameters(
        command=command, args=["--from", "paddleocr-mcp", "paddleocr_mcp"], env=env
    )
    # 约束整个生命周期，而不仅是 call_tool；stderr 可能含正文/凭证，不持久化。
    async with asyncio.timeout(OCR_TIMEOUT):
        with open(os.devnull, "w", encoding="utf-8") as errlog:
            async with stdio_client(params, errlog=errlog) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool(
                        "paddleocr_vl",
                        {
                            "input_data": str(path.resolve()),
                            "file_type": "pdf",
                            "output_mode": "simple",
                            "return_images": False,
                        },
                        read_timeout_seconds=OCR_TIMEOUT,
                    )
                    return _result_text(result)


def ocr_pdf(path: Path) -> str:
    """识别扫描件 PDF；空结果由 extract_text 判定，无本地 OCR 回退。

    此为同步后台任务 API；异步调用方应使用 asyncio.to_thread(ocr_pdf, path)。
    """
    token = settings.paddleocr_mcp_aistudio_access_token.strip()
    if not token:
        raise AppError(
            INTERNAL,
            "扫描件识别需要配置 PADDLEOCR_MCP_AISTUDIO_ACCESS_TOKEN",
            status=503,
        )
    command = shutil.which("uvx")
    if not command:
        raise AppError(INTERNAL, "PaddleOCR-VL 启动失败：请安装 uv 并确保 uvx 在 PATH 中", status=503)

    with tempfile.TemporaryDirectory(prefix="ia-paddleocr-") as tmp:
        upload_path = path.resolve()
        try:
            with pymupdf.open(path) as doc:
                pages = doc.page_count
                if doc.needs_pass or pages == 0:
                    raise ValueError("encrypted or empty PDF")
                if pages > OCR_MAX_PAGES:
                    upload_path = Path(tmp) / "resume.pdf"
                    with pymupdf.open() as limited:
                        limited.insert_pdf(doc, from_page=0, to_page=OCR_MAX_PAGES - 1)
                        limited.save(upload_path)
                    log.warning("PDF 共 %d 页，云端 OCR 只上传前 %d 页", pages, OCR_MAX_PAGES)
        except Exception as exc:
            raise AppError(RESUME_NO_TEXT, "PDF 无法读取，请确认文件未损坏且未加密", status=400) from exc

        try:
            text = asyncio.run(_recognize(upload_path, command, token))
        except Exception as exc:
            errors = _leaves(exc)
            for error in errors:
                if isinstance(error, AppError):
                    raise error from exc
            if any(isinstance(error, TimeoutError) for error in errors):
                raise AppError(UPSTREAM_TIMEOUT, "PaddleOCR-VL 识别超时，请稍后重试", status=504) from exc
            log.warning("PaddleOCR-VL 调用失败（%s）", type(errors[0]).__name__)
            raise AppError(INTERNAL, "PaddleOCR-VL 服务不可用，请检查网络、凭证与 uvx 配置", status=502) from exc

    log.info("PaddleOCR-VL 完成：处理 %d 页，共 %d 字符", min(pages, OCR_MAX_PAGES), len(text))
    return text
