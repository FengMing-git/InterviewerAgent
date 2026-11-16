"""真实简历解析计时脚本（手动运行，会消耗 LLM token）。

为什么放在 scripts/ 而不是 tests/：
  * 默认的 `uv run pytest` 不应该每次都真实调用 LLM、烧 token；
  * 这里用真实简历 + 项目 .env 里的真实模型，重复执行可观察耗时和解析质量。

用法（在 backend/ 目录下）：
    uv run python scripts/parse_resume_timing.py
    uv run python scripts/parse_resume_timing.py uploads/前端开发-谭轶.pdf
    uv run python scripts/parse_resume_timing.py <文件> --full

输出：
  * 文本抽取耗时、字符数；
  * LLM 结构化解析耗时；
  * 总耗时（抽取 + LLM）；
  * 本次 token 用量；
  * 画像摘要（姓名/学校/技能数/项目数/奖项数/实习数）。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

# 直接 `python scripts/xxx.py` 时 sys.path[0] 是 scripts/，这里补上 backend/
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# Windows 控制台默认可能是 GBK，统一成 UTF-8，避免中文输出乱码
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

from app.config import RESUME_TEXT_MAX_CHARS  # noqa: E402
from app.agent.llm import get_llm  # noqa: E402
from app.core.errors import AppError  # noqa: E402
from app.services.resume_parser import extract_text, parse_resume  # noqa: E402


def _default_resume() -> Path | None:
    """优先取 uploads/ 下最新的简历文件。"""
    upload_dir = BACKEND_DIR / "uploads"
    if not upload_dir.is_dir():
        return None
    candidates = [
        path
        for path in upload_dir.iterdir()
        if path.is_file() and path.suffix.lower() in {".pdf", ".docx", ".txt", ".md"}
    ]
    return max(candidates, key=lambda path: path.stat().st_mtime) if candidates else None


def _fmt_seconds(value: float) -> str:
    return f"{value:.2f} s"


def main() -> int:
    parser = argparse.ArgumentParser(description="用真实 LLM 解析一份简历并输出耗时")
    parser.add_argument("path", nargs="?", help="简历路径，缺省取 uploads/ 下最新文件")
    parser.add_argument("--full", action="store_true", help="额外打印完整画像 JSON")
    args = parser.parse_args()

    path = Path(args.path) if args.path else _default_resume()
    if path is None:
        print("[!] 没有找到简历文件，请显式传入路径", file=sys.stderr)
        return 2
    path = path.expanduser()
    if not path.is_file():
        print(f"[!] 文件不存在：{path}", file=sys.stderr)
        return 2

    print("=" * 72)
    print("真实简历解析计时")
    print("=" * 72)
    print(f"文件      : {path}")
    print(f"大小      : {path.stat().st_size / 1024:.1f} KB")
    print(f"模型      : 读取 backend/.env 中的 LLM_MODEL")
    print("-" * 72)

    overall_start = time.perf_counter()

    # ── 1. 文本抽取 ────────────────────────────────────────────────────
    try:
        extract_start = time.perf_counter()
        text = extract_text(path)
        extract_seconds = time.perf_counter() - extract_start
    except AppError as exc:
        print(f"[!] 文本抽取失败：code={exc.code} message={exc.message}")
        return 1

    used_chars = min(len(text), RESUME_TEXT_MAX_CHARS)
    print(f"文本抽取  : {_fmt_seconds(extract_seconds)}")
    print(f"原始字数  : {len(text)}")
    print(f"送入模型  : {used_chars} 字"
          + ("（已截断）" if len(text) > RESUME_TEXT_MAX_CHARS else ""))
    print("-" * 72)

    # ── 2. LLM 结构化解析 ─────────────────────────────────────────────
    try:
        llm = get_llm(None, purpose="parse")
        llm_start = time.perf_counter()
        profile, result = parse_resume(llm, text)
        llm_seconds = time.perf_counter() - llm_start
    except AppError as exc:
        print(f"[!] LLM 解析失败：code={exc.code} message={exc.message}")
        if exc.detail:
            print(f"    detail: {exc.detail}")
        return 1
    except Exception as exc:  # noqa: BLE001 —— 手动脚本，直接展示异常
        print(f"[!] LLM 解析异常：{type(exc).__name__}: {exc}")
        return 1

    total_seconds = time.perf_counter() - overall_start

    print(f"LLM 解析  : {_fmt_seconds(llm_seconds)}")
    print(f"总耗时    : {_fmt_seconds(total_seconds)}  ← 抽取 + LLM")
    print(f"Token     : 输入 {result.input_tokens} / 输出 {result.output_tokens} "
          f"/ 合计 {result.input_tokens + result.output_tokens}")
    print("-" * 72)

    # ── 3. 画像摘要（默认不打印简历全文，避免泄露隐私）──────────────────
    basic = profile.basic
    print("画像摘要")
    print(f"  姓名      : {basic.name or '-'}")
    print(f"  学校/专业 : {basic.school or '-'} / {basic.major or '-'}")
    print(f"  学历/毕业 : {basic.degree or '-'} / {basic.grad_date or '-'}")
    print(f"  技能      : {len(profile.skills)} 项"
          + (f"（{', '.join(profile.skills[:8])}…）" if len(profile.skills) > 8 else f"（{', '.join(profile.skills)}）" if profile.skills else ""))
    print(f"  项目      : {len(profile.projects)} 个")
    for project in profile.projects:
        print(f"    - {project.id} {project.name}（{project.role or '角色未知'}，"
              f"技术栈 {', '.join(project.tech_stack) or '未标注'}）")
    print(f"  奖项      : {len(profile.awards)} 项")
    print(f"  实习      : {len(profile.internships)} 段")

    if args.full:
        print("-" * 72)
        print(json.dumps(profile.model_dump(), ensure_ascii=False, indent=2))

    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())