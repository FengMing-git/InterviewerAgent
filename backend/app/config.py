"""全局配置出口（设计方案 §2「配置外置」）。

两类东西分开放，别混：

  * 因机器/环境而异、缺了就跑不起来的 → Settings 字段，从 backend/.env 读。
    **必填项不给默认值**：缺了在启动那一刻就抛 ValidationError 并点名缺哪个，
    而不是"跑起来看着正常、几分钟后第一次调 LLM 才失败"。
  * 调参用的数值（超时、轮数上限等）→ 本模块的普通常量，不暴露成环境变量。
    课设阶段要改直接改代码，省掉 .env 模板里一堆说明和"留空还是默认值"的歧义。

字段名小写 → 环境变量大写（例：llm_api_key ← LLM_API_KEY），对应 backend/.env.example。
.env 路径固定在 backend/.env（按 __file__ 推算，与运行目录无关），
因此无论从仓库根还是 backend/ 启动，读到的都是同一份配置。
取值优先级：真实环境变量 > .env 文件 > 字段默认值（pydantic-settings 内置）。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/ 目录（config.py → app/ → backend/）
BACKEND_DIR = Path(__file__).resolve().parents[1]
ENV_FILE = BACKEND_DIR / ".env"

# --------------------------------------------------------------------------- #
# 调参常量（不进 .env，改这里即可）
# --------------------------------------------------------------------------- #

# 上传白名单与大小上限（§3.1）
ALLOWED_EXTENSIONS: tuple[str, ...] = (".pdf", ".docx", ".txt", ".md")
UPLOAD_MAX_BYTES = 10 * 1024 * 1024

LLM_TIMEOUT = 120.0  # 单次生成硬超时（秒）
LLM_MAX_RETRIES = 2  # 调用失败重试次数
HTTP_TIMEOUT = 10.0  # 外部 HTTP 单请求超时（秒），GitHub / 网页抓取
RESEARCH_TIMEOUT = 120.0  # 联网核实整体硬超时（秒）→ 以已得证据落库，置 partial
RESEARCH_MAX_TOOL_CALLS = 12  # ReAct 工具调用轮数上限（防失控）
SQL_ECHO = False  # 打印 SQLAlchemy 生成的 SQL（排查建表/慢查询时改 True）


class Settings(BaseSettings):
    """必填项无默认值：缺任意一项，import 时就报错点名，启动即失败。"""

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,  # 从 .env 文件读（路径按 __file__ 算，与运行目录无关）
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",  # 系统里其它环境变量不干扰
    )

    # ── 必填（缺一不可，字段名大写即环境变量名）────────────────────────────
    llm_api_key: str
    llm_model: str
    llm_base_url: str
    tavily_api_key: str

    # ── 可选（缺失属于"优雅降级"，不是配置错误）────────────────────────────
    github_token: str = ""  # 不填则 GitHub 限流 60 次/h（§8 风险 1）
    http_proxy: str = ""
    # 默认值 = README 里那条 docker 起库命令，改过端口/密码才需要覆盖
    database_url: str = (
        "mysql+pymysql://root:ia123456@127.0.0.1:3306/intervieweragent?charset=utf8mb4"
    )

    # ── 派生路径 ───────────────────────────────────────────────────────────
    @property
    def upload_dir(self) -> Path:
        """uploads/：简历原件落盘处（uuid 命名，§4 隐私处理）"""
        return BACKEND_DIR / "uploads"

    @property
    def static_dir(self) -> Path:
        """static/：Next.js 静态导出产物，发布期由 FastAPI 托管（§1.2）"""
        return BACKEND_DIR / "static"

    @property
    def proxy_url(self) -> str | None:
        """httpx 要的是 None（直连）而不是空串，统一从这里取"""
        return self.http_proxy or None


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()