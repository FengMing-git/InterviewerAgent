"""数据库引擎与会话（Plan §3：core/db.py，SQLAlchemy 2.0 同步）。

单进程部署 + FastAPI 线程池跑同步接口，连接池用默认 QueuePool：
  * pool_pre_ping：每次取连接前 ping 一下，规避 MySQL wait_timeout 断连；
  * pool_recycle：8 小时强制重建，兜底长连接被服务端掐掉的情况。
"""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import SQL_ECHO, settings
from app.core.logger import get_logger

log = get_logger(__name__)

engine = create_engine(
    settings.database_url,
    echo=SQL_ECHO,
    pool_pre_ping=True,
    pool_recycle=3600,
    pool_size=5,
    max_overflow=10,
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,  # 提交后对象仍可读（响应序列化要用），避免二次查询
)


def get_db() -> Generator[Session, None, None]:
    """FastAPI 依赖：每个请求一个会话，请求结束自动归还。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
