"""简历原件落盘（Plan §4 隐私处理）：uploads/{uuid}{ext}，随机文件名，不入库。

入库的 ``interviews.resume_path`` 存相对 backend/ 的 POSIX 路径（如
``uploads/3f2a….pdf``），跨部署目录迁移不失效。30 天自动清理在 P5 落地。
"""

from __future__ import annotations

import uuid
from pathlib import Path

from app.config import settings
from app.core.logger import get_logger

log = get_logger(__name__)


def save_resume(data: bytes, ext: str) -> str:
    """写盘并返回相对路径（backend/uploads/{uuid}{ext}）。测试可替换本函数。"""
    upload_dir = settings.upload_dir
    upload_dir.mkdir(parents=True, exist_ok=True)
    relative = f"uploads/{uuid.uuid4().hex}{ext}"
    (upload_dir / Path(relative).name).write_bytes(data)
    log.info("简历已落盘：%s（%d 字节）", relative, len(data))
    return relative


def resolve_resume_path(relative: str | None) -> Path | None:
    """相对路径 → 绝对路径（不校验存在性）。"""
    if not relative:
        return None
    return settings.upload_dir / Path(relative).name


def remove_resume(relative: str | None) -> bool:
    """删除原件（删除面试时级联调用）；文件不存在时静默返回 False。"""
    path = resolve_resume_path(relative)
    if path is None:
        return False
    try:
        path.unlink(missing_ok=True)
        return True
    except OSError as exc:
        log.warning("删除简历原件失败：%s err=%s", path, exc)
        return False
