"""对比两种「无文本层 PDF → D1 结构化简历画像」流水线（手动运行，会消耗真实 LLM token）。

流水线 A：后端直接调用 PaddleOCR-VL 识别 PDF → 文本交给 LLM → 输出结构化画像
         （与线上 resume_parser 完全一致：extract_text + parse_resume）
流水线 B：把 PaddleOCR-VL MCP server 的工具（paddleocr_vl）原样注册给 LLM，
         LLM 自己决定调用参数、调用工具拿到识别结果，再输出结构化画像
         （langchain create_agent；结构化方式由 --b-format 决定：
           json（默认）= 与 A 同口径的「提示词 JSON Schema + Pydantic 校验 + 带错重试」，
                         两边使用同一云端 OCR，对比调用方式；
           tool        = ToolStrategy(ResumeProfile)，实测会随机输出空 skills/projects）

脚本最后会对比两条流水线的：耗时拆分、token、OCR 文本，以及画像逐字段差异。

用法（在 backend/ 目录下）：
    uv run python scripts/compare_ocr_pipelines.py                      # 默认取 uploads/ 最新 PDF
    uv run python scripts/compare_ocr_pipelines.py "uploads/前端开发-谭轶-图片版.pdf"
    uv run python scripts/compare_ocr_pipelines.py <pdf> --only b       # 只跑 B
    uv run python scripts/compare_ocr_pipelines.py <pdf> --full         # 打印完整画像 JSON
    uv run python scripts/compare_ocr_pipelines.py <pdf> --b-format tool  # B 改用 ToolStrategy

MCP 配置（按优先级）：
  1. --mcp-config 指定的 JSON（pi / Claude 的 mcpServers 格式，自动找 PaddleOCR 相关 server）
  2. 内置默认：uvx --from paddleocr-mcp paddleocr_mcp，模型 PaddleOCR-VL-1.6，来源 aistudio
  access token 取自 环境变量 > backend/.env 的 PADDLEOCR_MCP_AISTUDIO_ACCESS_TOKEN（不写死在脚本里）。

产物：OCR 文本、两份画像 JSON、MCP server 日志写到 uploads/_ocr_compare/<时间戳>/（已被 gitignore）。

计时口径：
  * 两条流程均不预热。A 的 OCR 耗时包含 MCP 启动、识别、关闭；
  * B 单列 MCP 启动；端到端对比请使用两边的总耗时，勿将 B 扣除启动后与 A 直接比较。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

# 直接 `python scripts/xxx.py` 时 sys.path[0] 是 scripts/，这里补上 backend/
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# Windows 控制台默认可能是 GBK，统一成 UTF-8，避免中文输出乱码
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

import pymupdf  # noqa: E402
from dotenv import dotenv_values  # noqa: E402
from langchain.agents import create_agent  # noqa: E402
from langchain.agents.structured_output import ToolStrategy  # noqa: E402
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage  # noqa: E402
from langchain_core.tools import StructuredTool  # noqa: E402
from pydantic import ValidationError  # noqa: E402

from app.agent.llm import _json_instruction, _parse_json_payload, get_llm  # noqa: E402
from app.agent.prompts import RESUME_PARSE_SYSTEM  # noqa: E402
from app.config import RESUME_TEXT_MAX_CHARS  # noqa: E402
from app.models.schemas import ResumeProfile, normalize_profile  # noqa: E402
from app.services.resume_parser import (  # noqa: E402
    _MIN_PDF_TEXT_CHARS,
    extract_text,
    parse_resume,
)

# 内置 MCP 配置（与用户提供的 mcpServers 配置一致，token 从环境 / .env 注入）
DEFAULT_MCP_SERVER: dict[str, Any] = {
    "command": "uvx",
    "args": ["--from", "paddleocr-mcp", "paddleocr_mcp"],
    "env": {
        "PADDLEOCR_MCP_MODEL": "PaddleOCR-VL-1.6",
        "PADDLEOCR_MCP_PPOCR_SOURCE": "aistudio",
    },
}
TOKEN_ENV = "PADDLEOCR_MCP_AISTUDIO_ACCESS_TOKEN"
MCP_CALL_TIMEOUT_S = 180.0
# 工具结果回给 LLM 的字符上限，和 A 送入 LLM 的上限保持一致，保证可比
TOOL_RESULT_MAX_CHARS = RESUME_TEXT_MAX_CHARS


@dataclass
class ToolCallRecord:
    args: dict[str, Any]
    seconds: float
    chars: int
    error: str | None = None


@dataclass
class RunResult:
    """一条流水线的计时与结果摘要。"""

    name: str
    total_s: float = 0.0
    server_start_s: float = 0.0
    ocr_s: float = 0.0
    llm_s: float = 0.0
    llm_rounds: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    ocr_text: str = ""
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    profile: ResumeProfile | None = None
    error: str | None = None
    notes: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# 工具函数
# --------------------------------------------------------------------------- #
def _default_pdf() -> Path | None:
    """优先取 uploads/ 下最新的 PDF。"""
    upload_dir = BACKEND_DIR / "uploads"
    if not upload_dir.is_dir():
        return None
    candidates = [p for p in upload_dir.iterdir() if p.is_file() and p.suffix.lower() == ".pdf"]
    return max(candidates, key=lambda p: p.stat().st_mtime) if candidates else None


def _pdf_info(path: Path) -> tuple[int, int]:
    """返回 (页数, 文本层字符数)，不触发 OCR。"""
    with pymupdf.open(path) as doc:
        pages = doc.page_count
        chars = len("\n".join(page.get_text() for page in doc).strip())
    return pages, chars


def _sum_usage(messages: list[Any]) -> tuple[int, int, int]:
    """累计 agent 多轮消息里的 token 用量，并返回 LLM 轮数。"""
    input_tokens = output_tokens = rounds = 0
    for message in messages:
        if not isinstance(message, AIMessage):
            continue
        rounds += 1
        usage = message.usage_metadata or {}
        input_tokens += int(usage.get("input_tokens") or 0)
        output_tokens += int(usage.get("output_tokens") or 0)
    return input_tokens, output_tokens, rounds


def _brief(text: str, limit: int = 300) -> str:
    text = " ".join(str(text).split())
    return text[:limit] + ("…" if len(text) > limit else "")


def _exc_text(exc: BaseException) -> str:
    """把 anyio / TaskGroup 的 ExceptionGroup 展开成可读文本。"""
    if isinstance(exc, BaseExceptionGroup):
        return "; ".join(_exc_text(e) for e in exc.exceptions)
    return f"{type(exc).__name__}: {exc}"


# --------------------------------------------------------------------------- #
# 流水线 A：后端直接调用 PaddleOCR-VL → 文本 → LLM
# --------------------------------------------------------------------------- #
def run_pipeline_a(pdf_path: Path) -> RunResult:
    result = RunResult(name="A 后端调用 PaddleOCR-VL → 文本 → LLM")
    t0 = time.perf_counter()
    try:
        text = extract_text(pdf_path)  # 无有效文字层时调用 PaddleOCR-VL
        result.ocr_s = time.perf_counter() - t0
        result.ocr_text = text

        t1 = time.perf_counter()
        profile, usage = parse_resume(get_llm(None, purpose="parse"), text)
        result.llm_s = time.perf_counter() - t1
    except Exception as exc:  # noqa: BLE001 —— 手动脚本，直接展示异常
        result.total_s = time.perf_counter() - t0
        result.error = _exc_text(exc)
        return result

    result.total_s = time.perf_counter() - t0
    result.llm_rounds = 1  # structured_generate 校验失败才会重试，这里无法区分，按 1 计
    result.input_tokens = usage.input_tokens
    result.output_tokens = usage.output_tokens
    result.profile = profile
    result.notes.append(
        f"送入 LLM 的文本：{min(len(text), RESUME_TEXT_MAX_CHARS)} 字符（OCR 原文 {len(text)}）"
    )
    result.notes.append("结构化方式：JSON Schema 写进提示词 + Pydantic 校验（线上同款）")
    return result


# --------------------------------------------------------------------------- #
# 流水线 B：LLM 自己调用 PaddleOCR-VL MCP 工具
# --------------------------------------------------------------------------- #
def _load_mcp_server(config_path: Path | None) -> tuple[dict[str, Any], str]:
    """返回 (server 配置 dict, 配置来源说明)；token 按 环境变量 > backend/.env 注入。"""
    server = json.loads(json.dumps(DEFAULT_MCP_SERVER))  # 深拷贝
    source = "内置默认配置"
    if config_path is not None:
        data = json.loads(config_path.read_text(encoding="utf-8"))
        servers = data.get("mcpServers") or {}
        for name, cfg in servers.items():
            cfg_env = cfg.get("env") or {}
            if "PADDLE" in name.upper() or any(k.startswith("PADDLEOCR_MCP_") for k in cfg_env):
                server = {
                    "command": cfg.get("command") or server["command"],
                    "args": list(cfg.get("args") or server["args"]),
                    "env": {**server["env"], **cfg_env},
                }
                source = f"{config_path} -> {name}"
                break
        else:
            raise RuntimeError(f"{config_path} 中没有 PaddleOCR 相关的 MCP server")

    if not server["env"].get(TOKEN_ENV):
        token = os.environ.get(TOKEN_ENV) or dotenv_values(BACKEND_DIR / ".env").get(TOKEN_ENV)
        if not token:
            raise RuntimeError(f"未找到 {TOKEN_ENV}：请设置环境变量或写入 backend/.env")
        server["env"][TOKEN_ENV] = token
    return server, source


def _mcp_result_to_text(mcp_result: Any) -> tuple[str, int]:
    """把 MCP CallToolResult 转成给 LLM 的纯文本；图片块不回传（避免 base64 撑爆上下文）。"""
    parts: list[str] = []
    dropped_images = 0
    for item in getattr(mcp_result, "content", None) or []:
        kind = getattr(item, "type", None)
        if kind == "text":
            parts.append(item.text)
        elif kind == "image":
            dropped_images += 1
        else:
            parts.append(f"[未处理的内容块：{kind}]")
    if dropped_images:
        parts.append(f"[已省略 {dropped_images} 张图片]")
    return "\n".join(parts), dropped_images


def _build_mcp_tool(session: Any, mcp_tool: Any, result: RunResult) -> StructuredTool:
    """把 MCP 工具按原名、原描述、原 input schema 注册成 LangChain 工具。"""

    async def _call(**kwargs: Any) -> str:
        t0 = time.perf_counter()
        record = ToolCallRecord(args=dict(kwargs), seconds=0.0, chars=0)
        result.tool_calls.append(record)
        try:
            mcp_result = await session.call_tool(
                mcp_tool.name, kwargs, read_timeout_seconds=MCP_CALL_TIMEOUT_S
            )
        except Exception as exc:  # noqa: BLE001 —— 错误交还给模型，让它自己决定是否重试
            record.error = _exc_text(exc)
            return f"工具调用失败：{record.error}"
        finally:
            record.seconds = time.perf_counter() - t0
            result.ocr_s += record.seconds

        text, _ = _mcp_result_to_text(mcp_result)
        if getattr(mcp_result, "is_error", False):
            record.error = _brief(text)
            return f"工具返回错误：{text}"
        record.chars = len(text)
        result.ocr_text = text  # 以最后一次成功调用为准
        if len(text) > TOOL_RESULT_MAX_CHARS:
            text = text[:TOOL_RESULT_MAX_CHARS] + "\n[结果过长，已截断]"
        return text

    return StructuredTool.from_function(
        coroutine=_call,
        name=mcp_tool.name,
        description=mcp_tool.description or "",
        args_schema=mcp_tool.input_schema,  # 原样使用 MCP 的 JSON Schema
    )


B_TOOL_HINT = (
    "\n\n【工具使用】你无法直接读取 PDF 文件。请先调用可用的 OCR 工具识别用户给出的简历文件，"
    "拿到原文后再按上面的规则输出结构化画像。识别成功后不要重复调用工具；"
    "识别结果中的 HTML / 图片标签、LaTeX 标记与简历内容无关，忽略即可。"
)


async def _json_final_with_retry(llm: Any, messages: list[Any], result: RunResult) -> ResumeProfile:
    """与 A 的 structured_generate 同口径：解析最终 JSON，校验失败把错误喂回模型重试一次。"""
    last_ai = next((m for m in reversed(messages) if isinstance(m, AIMessage)), None)
    if last_ai is None:
        raise RuntimeError("agent 没有产生任何 AI 消息")
    try:
        return ResumeProfile.model_validate(_parse_json_payload(last_ai.text))
    except (ValueError, ValidationError) as exc:
        result.notes.append(f"最终 JSON 校验失败，带错重试一次：{_brief(str(exc), 120)}")
        retry_messages = [
            SystemMessage(content=RESUME_PARSE_SYSTEM + "\n\n" + _json_instruction(ResumeProfile)),
            *messages,
            HumanMessage(
                content=(
                    f"上面的输出不满足要求：{_brief(str(exc))}。\n"
                    "请严格按要求重新输出，只输出一个 JSON 对象，不要任何解释、前后缀或 Markdown 代码块。"
                )
            ),
        ]
        reply = await llm.ainvoke(retry_messages)
        messages.append(reply)
        return ResumeProfile.model_validate(_parse_json_payload(reply.text))


async def run_pipeline_b_async(
    pdf_path: Path, server: dict[str, Any], log_path: Path, b_format: str
) -> RunResult:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    result = RunResult(name="B LLM 自主调用 PaddleOCR-VL MCP 工具")
    env = {**os.environ, "FASTMCP_SHOW_CLI_BANNER": "false", **server["env"]}
    params = StdioServerParameters(command=server["command"], args=server["args"], env=env)

    llm = get_llm(None, purpose="parse")
    if b_format == "json":
        # 与 A 相同：JSON Schema 写进提示词，最终回答是纯 JSON 文本
        system_prompt = RESUME_PARSE_SYSTEM + B_TOOL_HINT + "\n\n" + _json_instruction(ResumeProfile)
        response_format = None
    else:
        system_prompt = RESUME_PARSE_SYSTEM + B_TOOL_HINT
        response_format = ToolStrategy(ResumeProfile)

    messages: list[Any] = []
    profile: ResumeProfile | None = None
    start = time.perf_counter()
    agent_start = start
    try:
        # MCP server 的 stderr（日志、traceback）写入文件，避免刷屏
        with log_path.open("w", encoding="utf-8", errors="replace") as errlog:
            async with stdio_client(params, errlog=errlog) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = (await session.list_tools()).tools
                    result.server_start_s = time.perf_counter() - start
                    if not tools:
                        raise RuntimeError("MCP server 没有提供任何工具")
                    lc_tools = [_build_mcp_tool(session, t, result) for t in tools]
                    result.notes.append("注册给 LLM 的工具：" + ", ".join(t.name for t in tools))

                    agent = create_agent(
                        model=llm,
                        tools=lc_tools,
                        system_prompt=system_prompt,
                        response_format=response_format,
                    )
                    agent_start = time.perf_counter()
                    agent_result = await agent.ainvoke(
                        {
                            "messages": [
                                HumanMessage(
                                    content=(
                                        "请解析这份简历并输出结构化画像。\n"
                                        f"简历文件（本机绝对路径，PDF，无文字层）：{pdf_path}"
                                    )
                                )
                            ]
                        },
                        config={"recursion_limit": 12},
                    )
                    messages = list(agent_result.get("messages") or [])
                    if b_format == "json":
                        profile = await _json_final_with_retry(llm, messages, result)
                    else:
                        raw = agent_result.get("structured_response")
                        if isinstance(raw, dict):
                            raw = ResumeProfile.model_validate(raw)
                        if not isinstance(raw, ResumeProfile):
                            raise RuntimeError("agent 没有返回 structured_response")
                        profile = raw
    except BaseException as exc:  # noqa: BLE001 —— 含 ExceptionGroup
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        result.error = _exc_text(exc)

    end = time.perf_counter()
    result.total_s = end - start
    if agent_start > start:
        result.llm_s = max(end - agent_start - result.ocr_s, 0.0)
    result.input_tokens, result.output_tokens, result.llm_rounds = _sum_usage(messages)
    if profile is not None:
        result.profile = normalize_profile(profile)

    if not result.tool_calls:
        result.notes.append("警告：模型没有调用 OCR 工具，结果不可信")
    result.notes.append(
        "结构化方式：JSON Schema 写进提示词 + Pydantic 校验（与 A 同口径）"
        if b_format == "json"
        else "结构化方式：create_agent + ToolStrategy(ResumeProfile)（工具调用约束）"
    )
    result.notes.append(f"MCP server 日志：{log_path}")
    return result


# --------------------------------------------------------------------------- #
# 输出与对比
# --------------------------------------------------------------------------- #
def _print_profile(profile: ResumeProfile | None, full: bool) -> None:
    if profile is None:
        print("  画像      : 解析失败，没有结构化结果")
        return
    basic = profile.basic
    print(f"  姓名      : {basic.name or '-'}")
    print(f"  学校/专业 : {basic.school or '-'} / {basic.major or '-'}")
    print(f"  学历/毕业 : {basic.degree or '-'} / {basic.grad_date or '-'}")
    print(f"  技能      : {len(profile.skills)} 项")
    print(f"  项目      : {len(profile.projects)} 个")
    for project in profile.projects:
        print(f"    - {project.id} {project.name}（{project.role or '角色未知'}）")
    print(f"  奖项      : {len(profile.awards)} 项")
    print(f"  实习      : {len(profile.internships)} 段")
    if full:
        print(json.dumps(profile.model_dump(), ensure_ascii=False, indent=2))


def _print_result(result: RunResult, full: bool) -> None:
    print("=" * 78)
    print(result.name)
    print("=" * 78)
    if result.server_start_s:
        print(f"  总耗时    : {result.total_s:.2f} s"
              f"（不含 MCP 启动 {result.total_s - result.server_start_s:.2f} s）")
        print(f"  MCP 启动  : {result.server_start_s:.2f} s")
    else:
        print(f"  总耗时    : {result.total_s:.2f} s")
    print(f"  其中 OCR  : {result.ocr_s:.2f} s")
    print(f"  其中 LLM  : {result.llm_s:.2f} s（{result.llm_rounds} 轮）")
    print(
        f"  Token     : 输入 {result.input_tokens} / 输出 {result.output_tokens} "
        f"/ 合计 {result.input_tokens + result.output_tokens}"
    )
    print(f"  OCR 文本  : {len(result.ocr_text)} 字符")
    for i, call in enumerate(result.tool_calls, 1):
        status = f"失败：{_brief(call.error, 120)}" if call.error else f"{call.chars} 字符"
        print(f"  工具调用{i} : {call.seconds:.2f} s，{status}")
        print(f"              参数 {json.dumps(call.args, ensure_ascii=False)}")
    for note in result.notes:
        print(f"  备注      : {note}")
    if result.error:
        print(f"  错误      : {result.error}")
    _print_profile(result.profile, full)


def _norm(value: Any) -> str:
    # NFKC：全角括号/字母等折叠成半角，避免「javascript（es6+)」这类假差异
    return "".join(unicodedata.normalize("NFKC", str(value or "")).split()).lower()


def _diff_list(label: str, a_items: list[str], b_items: list[str]) -> None:
    a_map = {_norm(x): x for x in a_items}
    b_map = {_norm(x): x for x in b_items}
    common = a_map.keys() & b_map.keys()
    only_a = [a_map[k] for k in a_map if k not in common]
    only_b = [b_map[k] for k in b_map if k not in common]
    print(f"  {label}: A {len(a_items)} / B {len(b_items)} / 共同 {len(common)}")
    if only_a:
        print(f"    仅 A: {', '.join(only_a)}")
    if only_b:
        print(f"    仅 B: {', '.join(only_b)}")


def _compare_profiles(a: ResumeProfile, b: ResumeProfile) -> None:
    print("-" * 78)
    print("画像逐字段对比（忽略空白与大小写）")
    print("-" * 78)
    for key in ("name", "school", "major", "degree", "grad_date"):
        av, bv = getattr(a.basic, key), getattr(b.basic, key)
        mark = "✓" if _norm(av) == _norm(bv) else "✗"
        print(f"  {mark} basic.{key:<9}: A={av!r}  B={bv!r}")

    _diff_list("技能", a.skills, b.skills)
    _diff_list("项目名", [p.name for p in a.projects], [p.name for p in b.projects])
    for pa, pb in zip(a.projects, b.projects):
        print(f"    [{pa.id}] 角色 A={pa.role!r} B={pb.role!r}；时间 A={pa.period!r} B={pb.period!r}；"
              f"repo A={pa.repo_url!r} B={pb.repo_url!r}")
        _diff_list(f"  [{pa.id}] 技术栈", pa.tech_stack, pb.tech_stack)
    _diff_list("奖项", [x.name for x in a.awards], [x.name for x in b.awards])
    _diff_list(
        "实习",
        [f"{x.company}/{x.position}" for x in a.internships],
        [f"{x.company}/{x.position}" for x in b.internships],
    )


def _summary(a: RunResult, b: RunResult) -> None:
    print("=" * 78)
    print("对比汇总")
    print("=" * 78)
    b_warm = b.total_s - b.server_start_s
    rows = [
        ("总耗时 (s)", f"{a.total_s:.2f}", f"{b.total_s:.2f}"),
        ("总耗时-不含MCP启动 (s)", f"{a.total_s:.2f}", f"{b_warm:.2f}"),
        ("OCR (s)", f"{a.ocr_s:.2f}", f"{b.ocr_s:.2f}"),
        ("LLM (s)", f"{a.llm_s:.2f}", f"{b.llm_s:.2f}"),
        ("LLM 轮数", str(a.llm_rounds), str(b.llm_rounds)),
        ("输入 token", str(a.input_tokens), str(b.input_tokens)),
        ("输出 token", str(a.output_tokens), str(b.output_tokens)),
        ("OCR 文本字符", str(len(a.ocr_text)), str(len(b.ocr_text))),
    ]
    print(f"  {'指标':<22}{'A 直接OCR':>14}{'B MCP工具':>14}")
    for label, av, bv in rows:
        print(f"  {label:<22}{av:>14}{bv:>14}")
    if a.total_s > 0:
        print(f"  B/A 总耗时比：{b.total_s / a.total_s:.2f}（不含 MCP 启动 {b_warm / a.total_s:.2f}）")
    if a.profile and b.profile:
        _compare_profiles(a.profile, b.profile)
    print("=" * 78)


def _save_outputs(out_dir: Path, results: list[RunResult]) -> None:
    for r in results:
        tag = r.name[0].lower()
        (out_dir / f"{tag}_ocr.txt").write_text(r.ocr_text, encoding="utf-8")
        if r.profile is not None:
            (out_dir / f"{tag}_profile.json").write_text(
                json.dumps(r.profile.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8"
            )
    print(f"产物已保存：{out_dir}")


# --------------------------------------------------------------------------- #
def main() -> int:
    parser = argparse.ArgumentParser(description="对比 后端调用PaddleOCR→LLM 与 LLM自主调用PaddleOCR 两种简历解析流水线")
    parser.add_argument("path", nargs="?", help="简历 PDF 路径；缺省取 uploads/ 下最新 PDF")
    parser.add_argument("--mcp-config", type=Path, help="mcpServers 格式的 JSON；缺省用内置配置")
    parser.add_argument("--no-warm", action="store_true", help=argparse.SUPPRESS)  # 兼容旧命令，不再预热
    parser.add_argument("--only", choices=("a", "b"), help="只跑其中一条流水线")
    parser.add_argument("--full", action="store_true", help="额外打印完整画像 JSON")
    parser.add_argument(
        "--b-format",
        choices=("json", "tool"),
        default="json",
        help="B 的结构化方式：json（默认，与 A 同类格式约束，比较调用方式）/ "
        "tool（ToolStrategy 工具调用，实测会随机漏字段）",
    )
    args = parser.parse_args()

    pdf_path = Path(args.path) if args.path else _default_pdf()
    if pdf_path is None:
        print("[!] 没有找到 PDF，请传入路径", file=sys.stderr)
        return 2
    pdf_path = pdf_path.expanduser().resolve()  # MCP 工具要求绝对路径
    if not pdf_path.is_file():
        print(f"[!] 文件不存在：{pdf_path}", file=sys.stderr)
        return 2

    server: dict[str, Any] | None = None
    if args.only in (None, "b"):
        try:
            server, source = _load_mcp_server(args.mcp_config)
        except Exception as exc:  # noqa: BLE001
            print(f"[!] MCP 配置不可用：{exc}", file=sys.stderr)
            return 2

    out_dir = BACKEND_DIR / "uploads" / "_ocr_compare" / datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)

    pages, text_layer_chars = _pdf_info(pdf_path)
    print("=" * 78)
    print("简历解析流水线对比")
    print("=" * 78)
    print(f"文件        : {pdf_path}")
    print(f"大小        : {pdf_path.stat().st_size / 1024:.1f} KB，{pages} 页")
    print(f"文本层字符  : {text_layer_chars}"
          + ("（无文本层，需要 OCR）" if text_layer_chars < _MIN_PDF_TEXT_CHARS else "（有文本层，A 不会走 OCR）"))
    if server is not None:
        print(f"MCP 配置    : {source}（{server['command']} {' '.join(server['args'])}，"
              f"模型 {server['env'].get('PADDLEOCR_MCP_MODEL')}）")
    print("-" * 78)

    results: list[RunResult] = []
    if args.only in (None, "a"):
        print("[A] 后端调用 PaddleOCR-VL → LLM ……", flush=True)
        results.append(run_pipeline_a(pdf_path))
    if server is not None:
        print("[B] LLM 自主调用 PaddleOCR-VL MCP 工具 ……", flush=True)
        results.append(asyncio.run(run_pipeline_b_async(pdf_path, server, out_dir / "mcp_server.log", args.b_format)))

    for result in results:
        _print_result(result, args.full)
    if len(results) == 2:
        _summary(*results)
    _save_outputs(out_dir, results)
    return 0 if all(r.error is None for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
