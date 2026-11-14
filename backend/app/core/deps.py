"""通用依赖（Plan §3：core/deps.py）：鉴权 + 资源归属校验。"""

from __future__ import annotations

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.errors import FORBIDDEN, NOT_FOUND, UNAUTHORIZED, AppError
from app.core.security import decode_access_token
from app.models.orm import Interview, User

# auto_error=False：缺 header 时不让 FastAPI 抛 403，统一走 AppError(40101)
_bearer = HTTPBearer(auto_error=False)


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    """从 Authorization: Bearer <JWT> 解出当前用户；失败一律 40101。"""
    if creds is None:
        raise AppError(UNAUTHORIZED, "未登录或缺少访问令牌", status=401)
    user_id = decode_access_token(creds.credentials)  # 无效/过期 → 40101
    user = db.get(User, user_id)
    if user is None:
        raise AppError(UNAUTHORIZED, "登录状态已失效，请重新登录", status=401)
    return user


def get_owned_interview(
    interview_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Interview:
    """面试资源归属校验：不存在 → 40401；存在但不是本人的 → 40301。

    P1 的 interviews 路由按 `interview_id: int` 路径参数注入即可复用。
    """
    interview = db.get(Interview, interview_id)
    if interview is None:
        raise AppError(NOT_FOUND, "面试不存在", status=404)
    if interview.user_id != user.id:
        raise AppError(FORBIDDEN, "无权访问该面试", status=403)
    return interview
