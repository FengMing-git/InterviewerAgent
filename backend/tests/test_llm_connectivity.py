"""LLM 连通性冒烟测试：验证 backend/.env 里的配置能真正调通模型。

与单测套件里其它用例不同，本文件会发起**真实网络请求**（消耗少量 token），
因此做了分层处理：

  * 没有 backend/.env        → skip（机器没打算测，不影响 `uv run pytest` 全绿）
  * .env 存在但配置校验失败  → fail，让 ValidationError 原样抛出并点名缺哪项
  * .env 存在但必填项为空串  → fail，提示照模板填写

只跑本文件（-s 用于把模型回复打印到终端）：
    uv run pytest backend/tests/test_llm_connectivity.py -s

跑全量但跳过在线调用：
    uv run pytest -k "not ping"
"""

from __future__ import annotations

import pytest

# 四项必填与 config.py 中 Settings 字段一致（llm_api_key 等小写 ↔ LLM_API_KEY 大写）
REQUIRED_FIELDS = ("llm_api_key", "llm_model", "llm_base_url", "tavily_api_key")


@pytest.fixture(scope="module")
def settings():
    """加载真实配置：文件不存在则 skip，存在则让校验错误自然抛出（fail）。"""
    from app.config import ENV_FILE

    if not ENV_FILE.exists():
        pytest.skip(f"未找到 {ENV_FILE}，跳过 LLM 连通性测试")

    # 此刻才 import：config.py 在模块底部就会实例化并校验 Settings，
    # 缺必填项时 ValidationError 原样抛出 → 用例 fail 并点名缺哪个。
    from app.config import settings

    empty = [f.upper() for f in REQUIRED_FIELDS if not getattr(settings, f).strip()]
    assert not empty, (
        f".env 已存在但以下必填项为空串（照 .env.example 填真实值）：{', '.join(empty)}"
    )
    return settings


def test_settings_ready(settings):
    """离线检查：配置加载成功、四项必填非空、网关地址形如 URL。不发起网络请求。"""
    # key 只打印掩码，避免泄到终端/CI 日志
    masked = settings.llm_api_key[:6] + "***"
    print(f"\nmodel    = {settings.llm_model}")
    print(f"base_url = {settings.llm_base_url}")
    print(f"api_key  = {masked}")
    assert settings.llm_base_url.startswith(("http://", "https://")), (
        "LLM_BASE_URL 应以 http(s):// 开头"
    )


def test_llm_ping(settings):
    """在线检查：按 config 的模型名 / 网关 / 超时 / 重试参数真实调用一次 LLM。"""
    from langchain_openai import ChatOpenAI

    from app.config import LLM_MAX_RETRIES, LLM_TIMEOUT

    llm = ChatOpenAI(
        model=settings.llm_model,
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        timeout=LLM_TIMEOUT,
        max_retries=LLM_MAX_RETRIES,
    )
    reply = llm.invoke("连通性测试：请只回复 pong")
    text = reply.content.strip() if isinstance(reply.content, str) else str(reply.content)
    print(f"\n模型回复：{text!r}")
    print(f"usage：{reply.usage_metadata}")
    assert text, "模型返回了空内容，请检查 LLM_MODEL 是否为网关实际支持的模型名"
