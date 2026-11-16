"""测试夹具：独立测试库 + 应用依赖覆盖。

约定（Plan §10）：
  * 测试库用独立的库名 `intervieweragent_test`，不碰业务库；
  * 机器上没有 .env（或 MySQL 连不上）时数据库相关测试整体 skip，
    保证 `uv run pytest` 在任何机器上都能全绿退出——本机跑请先配好 .env；
  * 每个用例结束后清空所有表，用例之间互不串数据；
  * LLM 一律用假模型（``GenericFakeChatModel`` 预设输出），不发生真实调用。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.core.db import get_db
from app.main import create_app
from app.models.orm import Base

TEST_DB_NAME = "intervieweragent_test"


def register_and_login(client: TestClient, username: str = "tester01", password: str = "password123") -> dict[str, str]:
    """注册（或重复时登录）并返回 Bearer 头，供需要鉴权的接口测试复用。"""
    response = client.post("/api/v1/auth/register", json={"username": username, "password": password})
    if response.status_code == 409:  # 用户名已占用（同库重跑）
        response = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    token = response.json()["token"]
    return {"Authorization": f"Bearer {token}"}


def _load_settings():
    """配置不可用时返回 None：调用方据此 skip（不让 ValidationError 打爆收集阶段）。"""
    try:
        from app.config import settings

        return settings
    except Exception:
        return None


def _mysql_reachable(url: str) -> tuple[bool, str]:
    """探测 MySQL 服务器是否可连（密码错/没起库都算不可达）。"""
    try:
        server_engine = create_engine(url, pool_pre_ping=True, connect_args={"connect_timeout": 3})
        with server_engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        server_engine.dispose()
        return True, ""
    except Exception as exc:  # noqa: BLE001
        return False, str(exc).split("\n")[0]


@pytest.fixture(scope="session")
def db_engine():
    settings = _load_settings()
    if settings is None:
        pytest.skip("backend/.env 缺失或必填项不全，跳过数据库测试")

    base_url = make_url(settings.database_url)
    server_url = base_url.set(database="")
    ok, reason = _mysql_reachable(server_url)
    if not ok:
        pytest.skip(f"MySQL 不可连接（检查 .env 的 DATABASE_URL）：{reason}")

    # 建独立测试库（幂等）
    server_engine = create_engine(server_url, pool_pre_ping=True)
    with server_engine.connect() as conn:
        conn.execute(
            text(f"CREATE DATABASE IF NOT EXISTS {TEST_DB_NAME} CHARACTER SET utf8mb4")
        )
        conn.commit()
    server_engine.dispose()

    test_url = base_url.set(database=TEST_DB_NAME)
    engine = create_engine(test_url, pool_pre_ping=True)
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture()
def db(db_engine, monkeypatch) -> Session:
    """函数级会话；用例结束回滚 + 清表，保证隔离。

    同时把 ``app.core.db.SessionLocal`` 替换为测试库会话工厂：
    后台任务（services/tasks.task_session）运行时取的是这个符号，
    不替换的话任务线程会直接写业务库。
    """
    factory = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr("app.core.db.SessionLocal", factory)
    session = factory()
    try:
        yield session
    finally:
        session.rollback()
        for table in reversed(Base.metadata.sorted_tables):  # 先删子表
            session.execute(table.delete())
        session.commit()
        session.close()


@pytest.fixture()
def client(db: Session) -> TestClient:
    """TestClient，把 get_db 覆盖到测试库会话。

    不用 `with TestClient(...)`：避免触发 lifespan 里的 create_all（那会
    连业务库，而这里只想测接口逻辑本身，表已由 db_engine 建好）。
    """
    app = create_app()

    def _override_get_db():
        yield db

    app.dependency_overrides[get_db] = _override_get_db
    return TestClient(app)
