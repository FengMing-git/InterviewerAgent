"""后台任务公共设施（Plan §6.1/§6.7 的胶水层）。

FastAPI 接口返回 202 之后，业务任务通过 ``BackgroundTasks`` 在进程内执行
（Plan §1：单进程部署，不引入 Celery/Redis）。任务函数自行开会话、自行
管理状态位，本模块把这些公共动作收敛到这里：

  * ``task_session``         —— 任务专用会话（运行时取 ``db.SessionLocal``，测试可替换）
  * ``add_warning``          —— interviews.warnings 的并发安全追加（SELECT ... FOR UPDATE）
  * ``advance_after_preparation`` —— 解析+分析都完成后置 awaiting_confirm（Plan §6.1 ①）
  * ``run_preparation_tasks`` / ``retry_task`` —— 派发入口（API 层调用）
  * ``reset_zombie_tasks``   —— 启动时复位僵死任务（main.py lifespan 调用）

隐私纪律：日志只记 interview_id 与长度，不记简历内容。
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Generator
from contextlib import contextmanager
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import ZOMBIE_TASK_MINUTES
from app.core.logger import get_logger
from app.models.orm import Interview, User

log = get_logger(__name__)

# 后台任务状态机（SRS）：pending → running → done / partial / failed
TASK_STATUSES = ("parse", "analysis", "research", "init", "report")


@contextmanager
def task_session() -> Generator[Session, None, None]:
    """任务线程自己的会话。运行时从 ``app.core.db`` 取 SessionLocal，
    便于测试里把 SessionLocal 替换成测试库会话工厂。"""
    from app.core import db as db_module

    session = db_module.SessionLocal()
    try:
        yield session
    finally:
        session.close()


def add_warning(
    db: Session,
    interview_id: int,
    task: str,
    message: str,
    code: int | None = None,
    **extra: object,
) -> None:
    """并发安全地追加一条 warning（解析/分析两个线程可能同时写）。

    以 FOR UPDATE 独占该行读-改-写，避免丢更新；独立提交，即使调用方
    之后回滚也不会丢掉提示信息。
    """
    statement = select(Interview).where(Interview.id == interview_id).with_for_update()
    interview = db.execute(statement).scalar_one_or_none()
    if interview is None:
        return
    warning: dict[str, object] = {"task": task, "message": message}
    if code is not None:
        warning["code"] = code
    warning.update(extra)
    warnings = list(interview.warnings or [])
    warnings.append(warning)
    interview.warnings = warnings
    db.commit()


def merge_token_usage(interview_id: int, input_tokens: int, output_tokens: int) -> None:
    """把 token 用量累加进 interviews.token_usage（Plan §6.6）。

    独立会话 + FOR UPDATE：解析/分析两个线程可能同时完成，避免读-改-写丢更新。
    失败只告警——用量统计丢了不影响业务状态。
    """
    if input_tokens <= 0 and output_tokens <= 0:
        return
    try:
        with task_session() as db:
            statement = (
                select(Interview).where(Interview.id == interview_id).with_for_update()
            )
            interview = db.execute(statement).scalar_one_or_none()
            if interview is None:
                return
            usage = dict(interview.token_usage or {})
            usage["input"] = int(usage.get("input") or 0) + input_tokens
            usage["output"] = int(usage.get("output") or 0) + output_tokens
            interview.token_usage = usage
            db.commit()
    except Exception as exc:  # noqa: BLE001
        log.warning("token 用量累计失败：interview_id=%s err=%s", interview_id, exc)


def advance_after_preparation(interview_id: int) -> None:
    """简历解析与项目分析都「成功结束」后，把状态推进到 awaiting_confirm。

    结束的定义（Plan §6.1 ①）：parse 为 done，analysis 为 done/partial
    （部分仓库失败不影响确认画像）；任一 failed 则停在 preparing，等用户重试。
    """
    with task_session() as db:
        statement = select(Interview).where(Interview.id == interview_id).with_for_update()
        interview = db.execute(statement).scalar_one_or_none()
        if interview is None or interview.status != "preparing":
            return
        if interview.parse_status == "done" and interview.analysis_status in ("done", "partial"):
            interview.status = "awaiting_confirm"
            db.commit()
            log.info("准备阶段完成，进入待确认：interview_id=%s", interview_id)


def run_preparation_tasks(interview_id: int) -> None:
    """POST /interviews 的后台任务：简历解析 ∥ 项目分析，完成后推进状态。"""
    from app.services.project_analysis import run_analysis
    from app.services.resume_parser import run_parse

    _run_parallel(
        lambda: run_parse(interview_id),
        lambda: run_analysis(interview_id),
    )
    advance_after_preparation(interview_id)


def retry_task(interview_id: int, task: str) -> None:
    """POST /interviews/{id}/retry/{task} 的后台任务（P1 只支持 parse / analysis）。"""
    if task == "parse":
        from app.services.resume_parser import run_parse

        runner: Callable[[], None] = lambda: run_parse(interview_id)
    elif task == "analysis":
        from app.services.project_analysis import run_analysis

        runner = lambda: run_analysis(interview_id)
    else:  # research / init / report 随 P2/P4 落地
        log.warning("重试了未实现的任务类型：%s（interview_id=%s）", task, interview_id)
        return
    runner()
    advance_after_preparation(interview_id)


def _run_parallel(*fns: Callable[[], None]) -> None:
    """并行跑多个任务函数并等待全部结束（BackgroundTasks 本身是串行的）。"""
    threads = [threading.Thread(target=fn, name="ia-task", daemon=False) for fn in fns]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()


# --------------------------------------------------------------------------- #
# 启动期维护（main.py lifespan）
# --------------------------------------------------------------------------- #
def reset_zombie_tasks(db: Session) -> int:
    """启动时复位僵死任务（Plan §6.7）：

    * 任务状态为 running 且 updated_at 超过 ZOMBIE_TASK_MINUTES 分钟 → failed +
      warning「服务重启导致任务中断」，用户可通过 retry 接口重新执行；
    * ``generating=1`` 一律复位为 0（P3 的并发互斥标记）。

    返回处理的记录数（仅日志用）。
    """
    cutoff = datetime.now() - timedelta(minutes=ZOMBIE_TASK_MINUTES)
    handled = 0

    # 逐任务列判断（五个状态列任一 running 且超时即僵死）
    zombie_ids: set[int] = set()
    for column in (
        Interview.parse_status,
        Interview.analysis_status,
        Interview.research_status,
        Interview.init_status,
        Interview.report_status,
    ):
        rows = db.execute(
            select(Interview.id).where(Interview.updated_at < cutoff, column == "running")
        ).scalars().all()
        zombie_ids.update(rows)

    for interview_id in zombie_ids:
        interview = db.get(Interview, interview_id)
        if interview is None:
            continue
        for task in TASK_STATUSES:
            if getattr(interview, f"{task}_status") == "running":
                setattr(interview, f"{task}_status", "failed")
                add_warning(
                    db,
                    interview_id,
                    task,
                    "服务重启导致任务中断，可点击重试重新执行",
                )
                handled += 1
    db.execute(update(Interview).where(Interview.generating).values(generating=False))
    db.commit()
    if handled:
        log.info("启动期复位僵死任务 %d 个（阈值 %s 分钟）", handled, ZOMBIE_TASK_MINUTES)
    return handled


def load_user(db: Session, user_id: int) -> User | None:
    return db.get(User, user_id)
