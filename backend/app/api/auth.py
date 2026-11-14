"""注册 / 登录（Plan §7：POST /auth/register、/auth/login，无需 JWT）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import JWT_EXPIRE_HOURS
from app.core.db import get_db
from app.core.errors import UNAUTHORIZED, USERNAME_TAKEN, AppError
from app.core.logger import get_logger
from app.core.security import create_access_token, hash_password, verify_password
from app.models.orm import User
from app.models.schemas import LoginRequest, RegisterRequest, TokenResponse

log = get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["认证"])


@router.post("/register", response_model=TokenResponse, status_code=201)
def register(body: RegisterRequest, db: Session = Depends(get_db)) -> TokenResponse:
    """注册成功即视为登录（直接发 token，前端免二次跳转）。"""
    exists = db.scalar(select(User.id).where(User.username == body.username))
    if exists:
        raise AppError(USERNAME_TAKEN, "用户名已被占用", status=409)

    user = User(
        username=body.username,
        password_hash=hash_password(body.password),
        display_name=body.display_name,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    # 脱敏：只记用户名，不记密码/邮箱类信息
    log.info("用户注册：username=%s id=%s", user.username, user.id)
    return TokenResponse(token=create_access_token(user.id), expires_in=JWT_EXPIRE_HOURS * 3600)


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
    user = db.scalar(select(User).where(User.username == body.username))
    # 用户不存在与密码错误返回同一句话，避免账号枚举
    if user is None or not verify_password(body.password, user.password_hash):
        raise AppError(UNAUTHORIZED, "用户名或密码错误", status=401)
    return TokenResponse(token=create_access_token(user.id), expires_in=JWT_EXPIRE_HOURS * 3600)
