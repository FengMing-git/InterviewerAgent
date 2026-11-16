"""FastAPI 入口（Plan §3：main.py）。

  * lifespan：create_all 建表（课设阶段不用迁移工具）；僵尸任务/过期简历清理随 P1/P5 落地；
  * 路由统一挂 /api/v1 前缀；
  * 开发期 CORS 放行 next dev（3000 端口）；
  * 发布期托管 backend/static/（Next.js 静态导出产物）并做 SPA fallback。

本地启动：cd backend && uv run python -m app.main
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from starlette.staticfiles import StaticFiles

from app.api import auth, health, interviews, users
from app.config import settings
from app.core.db import SessionLocal, engine
from app.core.errors import NOT_FOUND, AppError, register_exception_handlers
from app.core.logger import get_logger
from app.models.orm import Base
from app.services.tasks import reset_zombie_tasks

log = get_logger(__name__)

API_PREFIX = "/api/v1"
# 开发期 next dev 的两个来源；发布期前后端同域，不需要 CORS
_DEV_ORIGINS = ["http://localhost:3000", "http://127.0.0.1:3000"]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # 建表（幂等）。数据库不可达不阻断启动：/health 会如实报 db_ok=false。
    try:
        Base.metadata.create_all(engine)
        log.info("数据库表已就绪（create_all 幂等）")
        # 僵尸任务复位（Plan §6.7）：上次进程挂掉时 running 的任务置 failed，
        # generating 一律归零；30 天简历清理随 P5 落地。
        with SessionLocal() as db:
            reset_zombie_tasks(db)
    except Exception as exc:  # noqa: BLE001 —— 启动期只告警不中断
        log.warning("启动期数据库维护失败（数据库不可达？）：%s", str(exc).splitlines()[0])
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="InterviewerAgent API", version="0.1.0", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=_DEV_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_exception_handlers(app)

    api = APIRouter(prefix=API_PREFIX)
    api.include_router(health.router)
    api.include_router(auth.router)
    api.include_router(users.router)
    api.include_router(interviews.router)
    app.include_router(api)

    _mount_spa(app)
    return app


def _mount_spa(app: FastAPI) -> None:
    """静态托管 Next.js 导出产物 + SPA fallback（Plan §8 发布形态）。

    backend/static/index.html 存在才启用（开发期没有产物，走 next dev + CORS）。
    assets/ 等 Next 带指纹的静态资源按文件直出；其余路径一律回退 index.html
    （静态导出没有服务端路由，参数都走查询字符串，见 Plan §8）。
    """
    static_dir = settings.static_dir
    index = static_dir / "index.html"
    if not index.is_file():
        log.info("未发现前端构建产物（%s），跳过静态托管；开发期请跑 next dev", index)
        return

    # 带 hash 的构建资源（/_next/static、favicon 等）挂文件服务，正确处理 ETag/HEAD
    app.mount("/_next", StaticFiles(directory=static_dir / "_next"), name="next-static")

    root = static_dir.resolve()

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str) -> FileResponse:
        if full_path.startswith(("api/", "_next/")):
            raise AppError(NOT_FOUND, "接口或资源不存在", status=404)
        if full_path:
            candidate = (static_dir / full_path).resolve()
            # 只放行 static 目录内的真实文件（防目录穿越）
            if candidate.is_file() and candidate.is_relative_to(root):
                return FileResponse(candidate)
        return FileResponse(index)

    log.info("静态托管已启用：%s（SPA fallback → index.html）", static_dir)


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)
