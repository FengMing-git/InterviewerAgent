"""Pydantic 数据契约（Plan §5）。

P0：认证与个人资料；P1 起补 D1 画像 / D2 项目分析报告与面试接口模型；
D3～D5、报告等结构随 P2～P4 里程碑补齐，字段命名与 SRS 表 3-3-2 对齐。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# 用户名规则（SRS）：4~32 位，字母、数字、下划线
USERNAME_PATTERN = r"^[A-Za-z0-9_]{4,32}$"


# --------------------------------------------------------------------------- #
# 认证（api/auth.py）
# --------------------------------------------------------------------------- #
class RegisterRequest(BaseModel):
    username: str = Field(pattern=USERNAME_PATTERN, description="4~32 位字母、数字或下划线")
    # 密码规则 SRS 未细化，先按 8~64 定，注册页同款提示
    password: str = Field(min_length=8, max_length=64, description="8~64 位")
    display_name: str | None = Field(default=None, max_length=64)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=1, max_length=64)


class TokenResponse(BaseModel):
    token: str
    expires_in: int  # 秒


# --------------------------------------------------------------------------- #
# 用户（api/users.py）
# --------------------------------------------------------------------------- #
class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    display_name: str | None = None
    target_position: str | None = None
    has_custom_llm: bool = False
    quota_date: date | None = None
    quota_used: int = 0


class UserProfileUpdate(BaseModel):
    """PUT /users/me：个人资料更新（自定义模型配置走独立接口，P5）。"""

    display_name: str | None = Field(default=None, max_length=64)
    target_position: str | None = Field(default=None, max_length=64)


# --------------------------------------------------------------------------- #
# D1 简历结构化画像（SRS 表 3-3-2；每场 1 份，存 interviews.profile）
# --------------------------------------------------------------------------- #
class BasicInfo(BaseModel):
    """basic{name, school, major, degree, grad_date(YYYY-MM)}。

    原文只写到年份就保留年份；GPA、排名等原文精度由 LLM 原样保留在对应字段。
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    name: str | None = None
    school: str | None = None
    major: str | None = None
    degree: str | None = None
    grad_date: str | None = Field(default=None, description="毕业年月，保留原文精度")


class ResumeProject(BaseModel):
    """画像中的项目条目；id 由代码统一重排为 P1..Pn（D2.related_project_id 引用）。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    id: str = Field(default="", description="项目编号 P1..Pn（入库前由代码赋值）")
    name: str
    role: str | None = None
    period: str | None = None
    tech_stack: list[str] = Field(default_factory=list)
    description: str | None = None
    repo_url: str | None = None


    @field_validator("tech_stack", mode="before")
    @classmethod
    def _clean_tech_stack(cls, v: list[str] | None) -> list[str]:
        # LLM 有时会吐空串/重复项，统一清理
        if not v:
            return []
        seen: list[str] = []
        for item in v:
            if isinstance(item, str) and item.strip() and item.strip() not in seen:
                seen.append(item.strip())
        return seen


class Award(BaseModel):
    """awards[{name, level, date}]。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    name: str
    level: str | None = None
    date: str | None = None


class Internship(BaseModel):
    """internships[]（SRS 未细化结构，按常见字段约定，均可空）。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    company: str | None = None
    position: str | None = None
    period: str | None = None
    description: str | None = None


class ResumeProfile(BaseModel):
    """D1 画像。所有字段可空：简历里没写就不编造。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    basic: BasicInfo = Field(default_factory=BasicInfo)
    skills: list[str] = Field(default_factory=list)
    projects: list[ResumeProject] = Field(default_factory=list)
    awards: list[Award] = Field(default_factory=list)
    internships: list[Internship] = Field(default_factory=list)

    @field_validator("skills", mode="before")
    @classmethod
    def _clean_skills(cls, v: list[str] | None) -> list[str]:
        if not v:
            return []
        seen: list[str] = []
        for item in v:
            if isinstance(item, str) and item.strip() and item.strip() not in seen:
                seen.append(item.strip())
        return seen


def normalize_profile(profile: ResumeProfile) -> ResumeProfile:
    """入库前统一化：项目 id 重排为 P1..Pn（顺序保持），供 D2 / D5 引用。"""

    for index, project in enumerate(profile.projects, start=1):
        project.id = f"P{index}"
    return profile


# --------------------------------------------------------------------------- #
# D2 项目分析报告（每仓库 1 份，存 interviews.project_reports 列表）
# --------------------------------------------------------------------------- #
class RepoMeta(BaseModel):
    """repo{url, stars, forks, language_ratio{}, last_commit_at}；数据全部来自 GitHub API。"""

    url: str
    stars: int | None = None
    forks: int | None = None
    language_ratio: dict[str, float] | None = Field(
        default=None, description="各语言占比（百分比，和≈100）"
    )
    last_commit_at: str | None = Field(default=None, description="最近一次提交时间 ISO8601")


class KeyModule(BaseModel):
    """key_modules[]：核心模块（名称 / 路径 / 一句话职责）。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    name: str
    path: str | None = None
    summary: str | None = None


class Finding(BaseModel):
    """findings[]：可核验事实，面试提问的证据来源（F-001 命名空间，入库前统一编号）。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    id: str = Field(default="", description="证据编号 F-001..（入库前由代码赋值）")
    text: str
    source_url: str


class ProjectReport(BaseModel):
    """D2 完整报告 = GitHub 元数据（代码填） + LLM 分析字段。"""

    repo: RepoMeta
    readme_summary: str | None = None
    tech_stack: list[str] = Field(default_factory=list)
    key_modules: list[KeyModule] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    related_project_id: str | None = Field(default=None, description="关联的 D1 项目 id（P1..）")


# --------------------------------------------------------------------------- #
# 面试接口（api/interviews.py）
# --------------------------------------------------------------------------- #
# SRS I4：人设风格为枚举；难度 1~3 整数；岗位如 后端/前端/算法
INTERVIEW_STYLES: tuple[str, ...] = ("严谨", "温和", "犀利")


class InterviewConfig(BaseModel):
    """面试配置（SRS I4）：{style, difficulty, position}。"""

    style: Literal["严谨", "温和", "犀利"] = "严谨"
    difficulty: Literal[1, 2, 3] = 2
    position: str = Field(default="后端", min_length=1, max_length=16)


class InterviewCreated(BaseModel):
    """POST /interviews 202 响应。"""

    id: int
    status: str


class InterviewSummary(BaseModel):
    """GET /interviews 列表项（首页历史面试）。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    status: str
    stage: int
    position: str | None = None
    total_score: float | None = None
    created_at: datetime


class InterviewDetail(BaseModel):
    """GET /interviews/{id}：向导页轮询用（Plan §7）。"""

    id: int
    status: str
    stage: int
    # 五个后台任务状态：pending / running / done / partial / failed
    parse_status: str
    analysis_status: str
    research_status: str
    init_status: str
    report_status: str

    repo_urls: list[str] | None = None
    profile: ResumeProfile | None = None
    project_reports: list[ProjectReport] | None = None
    verification_items: list[dict] | None = None  # D3（P2）
    persona: dict | None = None  # D4（P2）
    directions: list[dict] | None = None  # D5（P2）
    config: InterviewConfig | None = None
    warnings: list[dict] | None = None
    total_score: float | None = None
    created_at: datetime
    started_at: datetime | None = None
    ended_at: datetime | None = None


class ProfileConfirmRequest(BaseModel):
    """PUT /interviews/{id}/profile：确认画像 + 面试配置（Plan §6.1 ②）。"""

    profile: ResumeProfile
    config: InterviewConfig


class RetryAccepted(BaseModel):
    """POST /interviews/{id}/retry/{task} 202 响应。"""

    id: int
    task: str
    status: str
