"""云端 OCR 单测：不联网、不启动 uvx、不消耗 token。"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pymupdf
import pytest

from app.core.errors import AppError, INTERNAL, RESUME_NO_TEXT, UPSTREAM_TIMEOUT
from app.services import paddleocr, resume_parser


def make_pdf(path: Path, pages: int = 1, text: str = "") -> Path:
    with pymupdf.open() as pdf:
        for _ in range(pages):
            page = pdf.new_page()
            if text:
                page.insert_text((72, 72), text)
        pdf.save(path)
    return path


@pytest.fixture(autouse=True)
def isolated_ocr(monkeypatch):
    monkeypatch.setattr(paddleocr.settings, "paddleocr_mcp_aistudio_access_token", "fake-test-token")
    monkeypatch.setattr(paddleocr.shutil, "which", lambda name: "uvx")
    # 意外的真实 MCP 调用立即失败。
    def forbidden(*args, **kwargs):
        pytest.fail("测试禁止启动真实 MCP 服务")
    monkeypatch.setattr(paddleocr, "stdio_client", forbidden)


def test_scanned_pdf_calls_cloud(monkeypatch, tmp_path):
    path = make_pdf(tmp_path / "scan.pdf")
    ocr = Mock(return_value="张三，计算机专业，熟悉 Python FastAPI，参与订单系统开发")
    monkeypatch.setattr(resume_parser, "ocr_pdf", ocr)
    assert "Python" in resume_parser.extract_text(path)
    ocr.assert_called_once_with(path)


def test_corrupt_pdf_does_not_call_cloud(monkeypatch, tmp_path):
    path = tmp_path / "bad.pdf"
    path.write_bytes(b"not a pdf")
    ocr = Mock()
    monkeypatch.setattr(resume_parser, "ocr_pdf", ocr)
    with pytest.raises(AppError) as exc:
        resume_parser.extract_text(path)
    assert exc.value.code == RESUME_NO_TEXT
    ocr.assert_not_called()


def test_missing_token(monkeypatch, tmp_path):
    monkeypatch.setattr(paddleocr.settings, "paddleocr_mcp_aistudio_access_token", " ")
    with pytest.raises(AppError) as exc:
        paddleocr.ocr_pdf(tmp_path / "scan.pdf")
    assert exc.value.status == 503
    assert "PADDLEOCR_MCP_AISTUDIO_ACCESS_TOKEN" in exc.value.message


def test_missing_uvx(monkeypatch, tmp_path):
    monkeypatch.setattr(paddleocr.shutil, "which", lambda name: None)
    with pytest.raises(AppError) as exc:
        paddleocr.ocr_pdf(tmp_path / "scan.pdf")
    assert exc.value.status == 503
    assert "uvx" in exc.value.message


@pytest.mark.parametrize("pages", [1, 10, 12])
def test_page_limit_and_temp_cleanup(monkeypatch, tmp_path, pages):
    original = make_pdf(tmp_path / "scan.pdf", pages)
    seen = []
    async def recognize(path, command, token):
        seen.append(path)
        assert path.is_absolute()
        assert command == "uvx" and token == "fake-test-token"
        with pymupdf.open(path) as pdf:
            assert pdf.page_count == min(pages, paddleocr.OCR_MAX_PAGES)
        return "OCR 文本"
    monkeypatch.setattr(paddleocr, "_recognize", recognize)
    assert paddleocr.ocr_pdf(original) == "OCR 文本"
    with pymupdf.open(original) as pdf:
        assert pdf.page_count == pages
    if pages > paddleocr.OCR_MAX_PAGES:
        assert not seen[0].exists()
    else:
        assert seen[0] == original.resolve()


@pytest.mark.parametrize("error,code", [
    (TimeoutError(), UPSTREAM_TIMEOUT),
    (RuntimeError("secret fake-test-token"), INTERNAL),
    (ExceptionGroup("outer", [TimeoutError()]), UPSTREAM_TIMEOUT),
    (ExceptionGroup("outer", [AppError(INTERNAL, "OCR 失败", status=502)]), INTERNAL),
])
def test_errors_are_safe(monkeypatch, tmp_path, error, code):
    path = make_pdf(tmp_path / "scan.pdf", 12)
    seen = []
    async def recognize(path, *_):
        seen.append(path)
        raise error
    monkeypatch.setattr(paddleocr, "_recognize", recognize)
    with pytest.raises(AppError) as exc:
        paddleocr.ocr_pdf(path)
    assert exc.value.code == code
    assert "fake-test-token" not in exc.value.message
    assert not seen[0].exists()


def test_result_preserves_tables_and_links():
    response = SimpleNamespace(is_error=False, content=[
        SimpleNamespace(type="image", data="BASE64"),
        SimpleNamespace(type="text", text='<div><img src="x"></div>\n\n## 简历\n|技能|Python|\nhttps://example.com'),
    ])
    text = paddleocr._result_text(response)
    assert text.startswith("## 简历")
    assert "|技能|Python|" in text and "https://example.com" in text
    assert "<img" not in text and "BASE64" not in text


def test_tool_error_is_not_resume_text():
    response = SimpleNamespace(is_error=True, content=[SimpleNamespace(type="text", text="private error")])
    with pytest.raises(AppError) as exc:
        paddleocr._result_text(response)
    assert "private error" not in exc.value.message


def test_empty_result():
    assert paddleocr._result_text(SimpleNamespace(is_error=False, content=[])) == ""


def test_unexpected_result():
    with pytest.raises(AppError):
        paddleocr._result_text(SimpleNamespace())


@pytest.mark.asyncio
async def test_mcp_lifecycle_and_arguments(monkeypatch, tmp_path):
    events = []
    @asynccontextmanager
    async def stdio(params, errlog):
        assert params.env["PADDLEOCR_MCP_MODEL"] == "PaddleOCR-VL-1.6"
        assert params.env["PADDLEOCR_MCP_PPOCR_SOURCE"] == "aistudio"
        assert params.env["PADDLEOCR_MCP_AISTUDIO_ACCESS_TOKEN"] == "fake-test-token"
        events.append("start")
        try:
            yield (None, None)
        finally:
            events.append("stop")
    class Session:
        def __init__(self, *_): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_): events.append("close")
        async def initialize(self): events.append("init")
        async def call_tool(self, name, args, **kwargs):
            assert name == "paddleocr_vl"
            assert Path(args["input_data"]).is_absolute()
            assert args["file_type"] == "pdf"
            assert args["output_mode"] == "simple"
            assert args["return_images"] is False
            return SimpleNamespace(is_error=False, content=[SimpleNamespace(type="text", text="简历文本")])
    monkeypatch.setattr(paddleocr, "stdio_client", stdio)
    monkeypatch.setattr(paddleocr, "ClientSession", Session)
    assert await paddleocr._recognize(tmp_path / "scan.pdf", "uvx", "fake-test-token") == "简历文本"
    assert events == ["start", "init", "close", "stop"]


@pytest.mark.asyncio
async def test_startup_is_bounded(monkeypatch, tmp_path):
    @asynccontextmanager
    async def hangs(*args, **kwargs):
        await asyncio.sleep(1)
        yield (None, None)
    monkeypatch.setattr(paddleocr, "stdio_client", hangs)
    monkeypatch.setattr(paddleocr, "OCR_TIMEOUT", 0.01)
    with pytest.raises(TimeoutError):
        await paddleocr._recognize(tmp_path / "scan.pdf", "uvx", "fake-test-token")
