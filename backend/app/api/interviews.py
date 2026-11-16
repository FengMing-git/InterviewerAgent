"""面试 CRUD 与准备流程（Plan §7：/interviews 系列）。

P1 覆盖：创建（multipart 上传 + 仓库地址 → 202 后台解析 ∥ 分析）、
列表 / 详情（向导页轮询）、确认画像与面试配置、重试解析任务、删除。
SSE（start / answers）、跳过 / 结束、消息列表随 P3 落地。

上传校验顺序（SRS O4 错误码）：扩展名 40001 → 大小 40002 → 仓库地址 40000。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import ALLOWED_EXTENSIONS, MAX_REPOS, UPLOAD_MAX_BYTES
from app.core.db import get_db
from app.core.deps import get_current_user, get_owned_interview
from app.core.errors import PARAM_INVALID, RESUME_FORMAT_UNSUPPORTED, RESUME_TOO_LARGE, STATE_INVALID, AppError
from app.core.logger import get_logger
from app.models.orm import Interview, User
from app.models.schemas import (
    InterviewConfig,
    InterviewCreated,
    InterviewDetail,
    InterviewSummary,
    ProfileConfirmRequest,
    RetryAccepted,
    ResumeProfile,
    normalize_profile,
    ProjectReport,
)
from app.services import storage
from app.services.tasks import retry_task, run_preparation_tasks

log = get_logger(__name__)

router = APIRouter(prefix="/interviews", tags=["面试"])

# P1 可重试的任务；research / init 随 P2、report 随 P4 加入
RETRYABLE_TASKS = ("parse", "analysis")


@router.post("", response_model=InterviewCreated, status_code=202)
def create_interview(
    background_tasks: BackgroundTasks,
    resume: Annotated[UploadFile, File(description="简历文件（pdf/docx/txt/md，≤10MB）")],
    repo_urls: Annotated[list[str] | None, Form(description="GitHub 公开仓库地址，1~3 个")] = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> InterviewCreated:
    """上传简历 + 仓库地址 → 落盘建记录 → 后台并行「简历解析 ∥ 项目分析」。"""
    # ── 简历文件校验 ──────────────────────────────────────────────────────
    filename = resume.filename or ""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    dotted = f".{ext}"
    if dotted not in ALLOWED_EXTENSIONS:
        raise AppError(
            RESUME_FORMAT_UNSUPPORTED,
            f"简历格式不支持：仅支持 {' / '.join(ALLOWED_EXTENSIONS)}",
            status=400,
        )
    data = resume.file.read()
    if not data:
        raise AppError(RESUME_FORMAT_UNSUPPORTED, "简历文件为空", status=400)
    if len(data) > UPLOAD_MAX_BYTES:
        raise AppError(RESUME_TOO_LARGE, f"简历文件超过 {UPLOAD_MAX_BYTES // (1024 * 1024)} MB 上限", status=400)

    # ── 仓库地址校验（SRS I3）───────────────────────────────────────────
    urls = _validate_repo_urls(repo_urls)

    # ── 落盘 + 建记录 ────────────────────────────────────────────────────
    relative_path = storage.save_resume(data, dotted)
    interview = Interview(
        user_id=user.id,
        status="preparing",
        resume_path=relative_path,
        repo_urls=urls,
    )
    db.add(interview)
    db.commit()
    db.refresh(interview)
    log.info(
        "创建面试：interview_id=%s user_id=%s 简历 %d 字节 仓库 %d 个",
        interview.id,
        user.id,
        len(data),
        len(urls),
    )

    # ── 后台：简历解析 ∥ 项目分析（Plan §6.1 ①）─────────────────────────
    background_tasks.add_task(run_preparation_tasks, interview.id)
    return InterviewCreated(id=interview.id, status=interview.status)


@router.get("", response_model=list[InterviewSummary])
def list_interviews(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[InterviewSummary]:
    """本人历史面试（首页列表）。"""
    interviews = db.scalars(
        select(Interview)
        .where(Interview.user_id == user.id)
        .order_by(Interview.created_at.desc(), Interview.id.desc())
    ).all()
    return [_summary(iv) for iv in interviews]


@router.get("/{interview_id}", response_model=InterviewDetail)
def get_interview(
    interview: Interview = Depends(get_owned_interview),
) -> InterviewDetail:
    """详情：五个任务状态 + D1/D2（向导页轮询本接口，Plan §7）。"""
    return _detail(interview)


@router.put("/{interview_id}/profile", response_model=InterviewDetail)
def confirm_profile(
    body: ProfileConfirmRequest,
    interview: Interview = Depends(get_owned_interview),
    db: Session = Depends(get_db),
) -> InterviewDetail:
    """确认画像与面试配置（Plan §6.1 ②）。

    保存用户修改后的 D1 与 config，状态 → initializing。
    P2 在此派发「面试官初始化 + 联网核验」两个后台任务；
    P1 阶段两者尚不可用，状态停留在 initializing（init/research 保持 pending），
    准备页据实展示。
    """
    if interview.status != "awaiting_confirm":
        raise AppError(
            STATE_INVALID,
            "当前状态不允许确认画像（需先完成简历解析与项目分析）",
            detail={"status": interview.status},
            status=422,
        )

    profile = normalize_profile(body.profile)
    interview.profile = profile.model_dump()
    interview.config = body.config.model_dump()
    interview.status = "initializing"
    db.commit()
    db.refresh(interview)
    log.info("画像已确认：interview_id=%s config=%s", interview.id, body.config.model_dump())
    return _detail(interview)


@router.post("/{interview_id}/retry/{task}", response_model=RetryAccepted, status_code=202)
def retry_interview_task(
    task: str,
    background_tasks: BackgroundTasks,
    interview: Interview = Depends(get_owned_interview),
    db: Session = Depends(get_db),
) -> RetryAccepted:
    """重试失败的后台任务（Plan §6.7 / §7）：P1 支持 parse / analysis。"""
    if task not in RETRYABLE_TASKS:
        raise AppError(
            PARAM_INVALID,
            f"不支持的任务类型 {task!r}（当前支持：{' / '.join(RETRYABLE_TASKS)}）",
            status=400,
        )
    if interview.status != "preparing":
        raise AppError(
            STATE_INVALID,
            "当前状态不允许重试任务",
            detail={"status": interview.status},
            status=422,
        )
    if getattr(interview, f"{task}_status") != "failed":
        raise AppError(
            STATE_INVALID,
            f"任务 {task} 当前状态为 {getattr(interview, f'{task}_status')}，无需重试",
            status=422,
        )

    background_tasks.add_task(retry_task, interview.id, task)
    log.info("重试任务：interview_id=%s task=%s", interview.id, task)
    return RetryAccepted(id=interview.id, task=task, status=interview.status)


@router.delete("/{interview_id}", status_code=204)
def delete_interview(
    interview: Interview = Depends(get_owned_interview),
    db: Session = Depends(get_db),
) -> None:
    """删除面试：级联删除消息（外键 ON DELETE CASCADE）与简历原件。"""
    if interview.resume_path and storage.remove_resume(interview.resume_path):
        log.info("已删除简历原件：interview_id=%s", interview.id)
    db.delete(interview)
    db.commit()
    log.info("已删除面试：interview_id=%s", interview.id)


# --------------------------------------------------------------------------- #
# 内部工具
# --------------------------------------------------------------------------- #
def _validate_repo_urls(repo_urls: list[str] | None) -> list[str]:
    from app.agent.tools.github import parse_repo_url

    if not repo_urls:
        raise AppError(PARAM_INVALID, "请提供 1~3 个 GitHub 公开仓库地址", status=400)
    # 数量上限按原始提交条目判断（SRS I3：仅公开仓库 1~3 个）
    if not (1 <= len(repo_urls) <= MAX_REPOS):
        raise AppError(
            PARAM_INVALID,
            f"仓库地址数量须为 1~{MAX_REPOS} 个（收到 {len(repo_urls)} 个）",
            status=400,
        )
    # 去空白去重，保持顺序
    seen: dict[str, None] = {}
    for url in repo_urls:
        url = (url or "").strip()
        if url:
            seen.setdefault(url, None)
    urls = list(seen)
    if not urls:
        raise AppError(PARAM_INVALID, "仓库地址不能为空", status=400)
    for url in urls:
        parse_repo_url(url)  # 不合法 → 40000（报错信息含具体地址）
    return urls


def _summary(iv: Interview) -> InterviewSummary:
    return InterviewSummary(
        id=iv.id,
        status=iv.status,
        stage=iv.stage,
        position=(iv.config or {}).get("position"),
        total_score=float(iv.total_score) if iv.total_score is not None else None,
        created_at=iv.created_at,
    )


def _detail(iv: Interview) -> InterviewDetail:
    return InterviewDetail(
        id=iv.id,
        status=iv.status,
        stage=iv.stage,
        parse_status=iv.parse_status,
        analysis_status=iv.analysis_status,
        research_status=iv.research_status,
        init_status=iv.init_status,
        report_status=iv.report_status,
        repo_urls=iv.repo_urls,
        profile=ResumeProfile.model_validate(iv.profile) if iv.profile else None,
        project_reports=(
            [ProjectReport.model_validate(r) for r in iv.project_reports]
            if iv.project_reports
            else None
        ),
        verification_items=iv.verification_items,
        persona=iv.persona,
        directions=iv.directions,
        config=InterviewConfig.model_validate(iv.config) if iv.config else None,
        warnings=iv.warnings,
        total_score=float(iv.total_score) if iv.total_score is not None else None,
        created_at=iv.created_at,
        started_at=iv.started_at,
        ended_at=iv.ended_at,
    )
