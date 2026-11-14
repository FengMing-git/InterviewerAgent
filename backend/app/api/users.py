"""个人资料（Plan §7：GET / PUT /users/me）。自定义模型配置接口 P5 再加。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import get_current_user
from app.models.orm import User
from app.models.schemas import UserOut, UserProfileUpdate

router = APIRouter(prefix="/users", tags=["用户"])


@router.get("/me", response_model=UserOut)
def get_me(user: User = Depends(get_current_user)) -> UserOut:
    return UserOut(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        target_position=user.target_position,
        has_custom_llm=user.has_custom_llm,
        quota_date=user.quota_date,
        quota_used=user.quota_used,
    )


@router.put("/me", response_model=UserOut)
def update_me(
    body: UserProfileUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UserOut:
    """部分更新：请求里没给的字段（None）不动。"""
    if body.display_name is not None:
        user.display_name = body.display_name
    if body.target_position is not None:
        user.target_position = body.target_position
    db.commit()
    db.refresh(user)
    return get_me(user)
