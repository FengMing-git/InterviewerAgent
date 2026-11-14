"""日志配置（Plan §3：core/logger.py）。

脱敏纪律（写代码时遵守，框架只能提醒不能强制）：
  * 不打印密码、JWT、API Key、AES 密文——需要标识用户时只记 username / user_id；
  * 不打印简历全文与画像 D1 原文——最多记抽取出的字符数；
  * LLM 提示词与候选人回答只记长度，不记内容。
"""

from __future__ import annotations

import logging

_CONFIGURED = False


def _configure_once() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # uvicorn / sqlalchemy 的噪声级别收敛
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    _CONFIGURED = True


_configure_once()


def get_logger(name: str) -> logging.Logger:
    """统一入口：`logger = get_logger(__name__)`。"""
    _configure_once()
    return logging.getLogger(name)
