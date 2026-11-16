"""项目分析服务（Plan §6.1 ①；SRS 功能模块 3，M5 的分析部分）。

每个仓库的流水线（Plan §6.1）：
  元数据 + 语言占比 + README + 递归目录树
    → LLM 从目录树挑 ≤5 个关键文件 → 逐个读取
    → LLM 生成 D2（readme_summary / tech_stack / key_modules / findings /
      related_project_id），repo{} 指标由代码从 API 结果填充
    → findings 的 source_url 校验（必须属于本次运行出现过的 GitHub URL）

降级策略（SRS 3.5 / 表 3-2-2）：
  * 仓库不存在或私有（404）→ 42201，该仓库无报告；
  * 限流 / 超时 → 保留已获取的部分继续，任务标 partial；
  * 全部仓库都失败 → analysis_status=failed，可重试。

findings 编号：入库前统一重排为 F-001..（命名空间见 Plan §5）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from app.agent.llm import get_llm, structured_generate
from app.agent.prompts import (
    KEY_FILES_SYSTEM,
    PROJECT_REPORT_SYSTEM,
    key_files_user_prompt,
    project_report_user_prompt,
)
from app.agent.tools.github import GitHubClient, default_client, parse_repo_url
from app.config import (
    FILE_CONTENT_MAX_CHARS,
    FINDINGS_PER_REPO_MAX,
    GITHUB_FILE_MAX_BYTES,
    GITHUB_README_MAX_BYTES,
    KEY_FILES_MAX,
    README_MAX_CHARS,
    TREE_MAX_ENTRIES,
)
from app.core.errors import AppError
from app.core.logger import get_logger
from app.models.orm import Interview
from app.models.schemas import Finding, KeyModule, ProjectReport, RepoMeta
from app.services.tasks import add_warning, load_user, merge_token_usage, task_session

log = get_logger(__name__)

# 关键文件启发式兜底时偏好的代码后缀（LLM 挑不出/输出不合法时用）
_CODE_SUFFIXES = (
    ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".go", ".rs", ".c", ".cc",
    ".cpp", ".h", ".cs", ".rb", ".php", ".kt", ".swift", ".sql", ".sh",
)
_ENTRY_HINTS = ("main", "index", "app", "server", "core", "config", "settings")


class _KeyFiles(BaseModel):
    """第一步 LLM 输出：关键文件路径列表。"""

    files: list[str] = Field(default_factory=list, max_length=KEY_FILES_MAX)


class _ReportDraft(BaseModel):
    """第二步 LLM 输出：D2 中由模型负责的字段（repo{} 指标由代码填充）。"""

    readme_summary: str | None = None
    tech_stack: list[str] = Field(default_factory=list)
    key_modules: list[KeyModule] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    related_project_id: str | None = None


@dataclass(slots=True)
class RepoAnalysis:
    """单个仓库的分析结果：报告草稿 + 过程提示（进 warnings）+ token 用量。"""

    report: ProjectReport
    notes: list[str] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0


def analyze_repo(llm: Any, client: GitHubClient, repo_url: str, profile: dict | None) -> RepoAnalysis:
    """分析单个仓库。仓库级硬失败（404 / LLM 两次不可解析）抛 AppError；
    过程中的软失败（限流、超时、单个文件读取失败）记进 notes，报告保留已有部分。
    """
    owner, repo = parse_repo_url(repo_url)
    notes: list[str] = []
    input_tokens = output_tokens = 0

    # ── 1. 元数据（404 → 42201，硬失败）─────────────────────────────────
    meta = client.get_repo(owner, repo)  # 42201 / 42901 / 50401
    branch = meta["default_branch"]
    repo_page = meta["html_url"] or f"https://github.com/{owner}/{repo}"
    blob_template = f"{repo_page}/blob/{branch}/"

    # ── 2. 语言占比（软失败）────────────────────────────────────────────
    language_ratio: dict[str, float] | None = None
    try:
        language_ratio = client.get_languages(owner, repo) or None
    except AppError as exc:
        notes.append(f"语言统计获取失败（{exc.message}），已跳过")

    # ── 3. README（软失败）──────────────────────────────────────────────
    readme_text: str | None = None
    try:
        readme_text = client.get_readme(owner, repo, max_bytes=GITHUB_README_MAX_BYTES)
    except AppError as exc:
        notes.append(f"README 获取失败（{exc.message}），已跳过")
    if readme_text is None:
        readme_text = "（未获取到 README）"

    # ── 4. 目录树（软失败：拿不到就没有关键文件环节）──────────────────────
    tree: list[dict[str, Any]] = []
    tree_ok = True
    try:
        tree = client.list_tree(owner, repo, branch, max_entries=TREE_MAX_ENTRIES)
    except AppError as exc:
        tree_ok = False
        notes.append(f"目录树获取失败（{exc.message}），关键文件环节已跳过")
    tree_paths = {entry["path"] for entry in tree if entry.get("type") == "blob"}

    # ── 5. LLM 挑关键文件 → 读取 ────────────────────────────────────────
    file_contents: dict[str, str] = {}
    if tree_ok and tree_paths:
        key_paths, usage = _select_key_files(llm, meta, readme_text, tree)
        input_tokens += usage[0]
        output_tokens += usage[1]
        if key_paths is None:
            notes.append("模型未能选出关键文件，已按启发式规则选取")
            key_paths = _heuristic_key_files(tree)
        for path in key_paths[:KEY_FILES_MAX]:
            try:
                content = client.read_file(
                    owner, repo, path, branch, max_bytes=GITHUB_FILE_MAX_BYTES
                )
            except AppError as exc:
                notes.append(f"文件读取失败：{path}（{exc.message}）")
                continue
            if content is None:
                notes.append(f"文件不可读（二进制或过大）：{path}")
                continue
            file_contents[path] = content[:FILE_CONTENT_MAX_CHARS]

    # ── 6. LLM 生成 D2（硬失败：两次都不可解析 → 本仓库无报告）───────────
    draft, usage = _generate_report_draft(llm, meta, readme_text, file_contents, profile, blob_template)
    input_tokens += usage[0]
    output_tokens += usage[1]

    report = ProjectReport(
        repo=RepoMeta(
            url=repo_page,
            stars=meta.get("stars"),
            forks=meta.get("forks"),
            language_ratio=language_ratio,
            last_commit_at=meta.get("pushed_at"),
        ),
        readme_summary=draft.readme_summary,
        tech_stack=draft.tech_stack,
        key_modules=[m for m in draft.key_modules if _module_ok(m, tree_paths, file_contents)],
        findings=_sanitize_findings(draft.findings, repo_page, blob_template, file_contents),
        related_project_id=_valid_project_id(draft.related_project_id, profile),
    )
    return RepoAnalysis(
        report=report, notes=notes, input_tokens=input_tokens, output_tokens=output_tokens
    )


# --------------------------------------------------------------------------- #
# 后台任务入口
# --------------------------------------------------------------------------- #
def run_analysis(interview_id: int) -> None:
    """POST /interviews / retry(analysis) 的后台任务：逐仓库分析 → 汇总入库。"""
    with task_session() as db:
        interview = db.get(Interview, interview_id)
        if interview is None or not interview.repo_urls:
            log.warning("项目分析任务丢弃：interview_id=%s 无仓库地址", interview_id)
            return
        repo_urls = list(interview.repo_urls)

        interview.analysis_status = "running"
        db.commit()

        input_tokens = output_tokens = 0
        analyses: list[RepoAnalysis] = []
        failures: list[str] = []

        user = load_user(db, interview.user_id)
        try:
            llm = get_llm(user, purpose="parse")  # 分析用低温度（Plan §6.6）
            with default_client() as client:
                for repo_url in repo_urls:
                    # profile 可能仍在并行解析中；每次取最新值，尽量给 LLM 提供关联信息
                    profile = db.get(Interview, interview_id).profile
                    try:
                        analysis = analyze_repo(llm, client, repo_url, profile)
                        analyses.append(analysis)
                        input_tokens += analysis.input_tokens
                        output_tokens += analysis.output_tokens
                        for note in analysis.notes:
                            add_warning(db, interview_id, "analysis", note, repo=repo_url)
                    except AppError as exc:
                        failures.append(f"{repo_url}：{exc.message}")
                        add_warning(db, interview_id, "analysis", exc.message, exc.code, repo=repo_url)

            reports = [analysis.report for analysis in analyses]
            _renumber_findings(reports)

            if analyses:
                # 至少一个仓库有报告：partial（有仓库失败或有软降级），否则 done
                has_partial = bool(failures) or any(a.notes for a in analyses)
                new_status = "partial" if has_partial else "done"
                interview = db.get(Interview, interview_id)
                interview.project_reports = [r.model_dump() for r in reports]
                interview.analysis_status = new_status
                db.commit()
                merge_token_usage(interview_id, input_tokens, output_tokens)
                log.info(
                    "项目分析完成：interview_id=%s 仓库 %d/%d 成功，状态 %s",
                    interview_id,
                    len(analyses),
                    len(repo_urls),
                    new_status,
                )
            else:
                _fail_analysis(db, interview_id, f"所有仓库分析失败：{'；'.join(failures)}")
        except AppError as exc:
            _fail_analysis(db, interview_id, exc.message, exc.code)
        except Exception as exc:  # noqa: BLE001
            log.exception("项目分析任务异常：interview_id=%s", interview_id)
            _fail_analysis(db, interview_id, f"项目分析异常：{exc}")


def _fail_analysis(db, interview_id: int, message: str, code: int | None = None) -> None:
    db.rollback()
    interview = db.get(Interview, interview_id)
    if interview is not None:
        interview.analysis_status = "failed"
        db.commit()
    add_warning(db, interview_id, "analysis", message, code)


# --------------------------------------------------------------------------- #
# 内部步骤
# --------------------------------------------------------------------------- #
def _select_key_files(
    llm: Any, meta: dict[str, Any], readme_text: str, tree: list[dict[str, Any]]
) -> tuple[list[str] | None, tuple[int, int]]:
    """LLM 从目录树挑关键文件；输出不可解析/路径全不合法时返回 None（走启发式）。"""
    tree_text = "\n".join(
        f"{entry.get('path')}  ({entry.get('type')}, {entry.get('size')}B)" for entry in tree
    )
    repo_context = _repo_context_text(meta)
    try:
        result = structured_generate(
            llm,
            KEY_FILES_SYSTEM,
            key_files_user_prompt(repo_context, tree_text),
            _KeyFiles,
            purpose="关键文件选择",
        )
    except AppError as exc:
        log.warning("关键文件选择失败，走启发式：%s", exc.message)
        return None, (0, 0)

    tree_paths = {entry.get("path") for entry in tree if entry.get("type") == "blob"}
    valid = [p for p in result.parsed.files if p in tree_paths]
    return (valid or None), (result.input_tokens, result.output_tokens)


def _heuristic_key_files(tree: list[dict[str, Any]]) -> list[str]:
    """兜底：LLM 挑不出关键文件时按简单规则选取（名字带入口提示的优先、
    代码文件优先、目录层级浅的优先）。"""

    def score(path: str) -> tuple[int, int, int]:
        name = path.rsplit("/", 1)[-1].lower()
        hint = int(any(h in name for h in _ENTRY_HINTS))
        code = int(path.endswith(_CODE_SUFFIXES))
        shallow = int(path.count("/") <= 2)
        return (hint, code, shallow)

    files = [entry["path"] for entry in tree if entry.get("type") == "blob" and entry.get("path")]
    files.sort(key=score, reverse=True)
    return files[:KEY_FILES_MAX]


def _generate_report_draft(
    llm: Any,
    meta: dict[str, Any],
    readme_text: str,
    file_contents: dict[str, str],
    profile: dict | None,
    blob_template: str,
) -> tuple[_ReportDraft, tuple[int, int]]:
    """LLM 生成 D2 的模型侧字段；失败重试后仍失败 → 抛 AppError（该仓库无报告）。"""
    repo_context = _repo_context_text(meta)
    files_text = "\n\n".join(
        f"── 文件：{path}（在线地址：{blob_template}{path}）\n{content}"
        for path, content in file_contents.items()
    )
    resume_projects_text = _resume_projects_text(profile)
    result = structured_generate(
        llm,
        PROJECT_REPORT_SYSTEM,
        project_report_user_prompt(repo_context, readme_text[:README_MAX_CHARS], files_text, resume_projects_text),
        _ReportDraft,
        purpose="项目分析报告",
    )
    return result.parsed, (result.input_tokens, result.output_tokens)


def _repo_context_text(meta: dict[str, Any]) -> str:
    parts = [
        f"仓库：{meta.get('full_name')}",
        f"主页：{meta.get('html_url')}",
        f"默认分支：{meta.get('default_branch')}",
        f"stars：{meta.get('stars')}，forks：{meta.get('forks')}",
        f"主题：{', '.join(meta.get('topics') or []) or '无'}",
        f"最近推送：{meta.get('pushed_at')}",
        f"简介：{meta.get('description') or '无'}",
    ]
    return "\n".join(parts)


def _resume_projects_text(profile: dict | None) -> str:
    if not profile:
        return ""
    lines = []
    for project in profile.get("projects") or []:
        repo_url = project.get("repo_url") or "无仓库地址"
        tech = ", ".join(project.get("tech_stack") or []) or "未标注"
        lines.append(
            f"- {project.get('id')}：{project.get('name')}（{project.get('role') or '角色未知'}，"
            f"技术栈：{tech}，仓库：{repo_url}）"
        )
    return "\n".join(lines)


def _module_ok(module: KeyModule, tree_paths: set[str | None], file_contents: dict[str, str]) -> bool:
    """key_modules 校验：名字非空；path 必须是本次读过的文件或目录树中的路径。"""
    if not module.name:
        return False
    if module.path is None:
        return True
    return module.path in tree_paths or module.path in file_contents


def _sanitize_findings(
    findings: list[Finding], repo_page: str, blob_template: str, file_contents: dict[str, str]
) -> list[Finding]:
    """findings 裁剪与校验：

    * 数量上限 FINDINGS_PER_REPO_MAX；
    * source_url 只允许本次出现过的 URL（仓库主页 / blob 文件地址）——
      不在集合里的丢弃（证据白名单思想的轻量版，Plan §6.2 同款纪律）。
    """
    allowed = {repo_page} | {f"{blob_template}{path}" for path in file_contents}
    kept = [f for f in findings if f.text and f.source_url in allowed]
    dropped = len(findings) - len(kept)
    if dropped:
        log.info("丢弃 %d 条 source_url 不在证据集合内的 findings", dropped)
    return kept[:FINDINGS_PER_REPO_MAX]


def _valid_project_id(related_project_id: str | None, profile: dict | None) -> str | None:
    if not related_project_id or not profile:
        return None
    valid_ids = {p.get("id") for p in profile.get("projects") or []}
    return related_project_id if related_project_id in valid_ids else None


def _renumber_findings(reports: list[ProjectReport]) -> None:
    """F-001 起跨仓库统一编号（Plan §5 证据 ID 命名空间，与存库顺序一致）。"""
    counter = 1
    for report in reports:
        for finding in report.findings:
            finding.id = f"F-{counter:03d}"
            counter += 1
