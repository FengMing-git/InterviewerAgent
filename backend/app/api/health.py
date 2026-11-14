"""健康检查（Plan §7：GET /health，免鉴权，不调用 LLM）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.core.db import get_db
from app.core.logger import get_logger

log = get_logger(__name__)

router = APIRouter(tags=["健康检查"])


@router.get("/health")
def health(db: Session = Depends(get_db)) -> dict:
    """检查配置是否齐全、数据库是否连通。任何一项不满足返回 degraded（HTTP 仍 200，
    方便前端/运维区分「服务活着」与「依赖就绪」）。"""
    config_ok = all(
        [
            settings.llm_api_key.strip(),
            settings.llm_model.strip(),
            settings.llm_base_url.strip(),
            settings.tavily_api_key.strip(),
            settings.jwt_secret.strip(),
            settings.app_encryption_key.strip(),
        ]
    )
    db_ok = True
    try:
        db.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 —— 健康检查就是要吞掉一切连接错误
        db_ok = False
        log.warning("健康检查：数据库连接失败：%s", exc)

    ok = config_ok and db_ok
    return {
        "status": "ok" if ok else "degraded",
        "config_ok": config_ok,
        "db_ok": db_ok,
    }
