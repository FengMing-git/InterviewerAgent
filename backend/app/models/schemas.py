"""Pydantic 数据契约（Plan §5）。

P0 骨架只放认证与个人资料部分；D1~D5、报告等结构随 P1~P4 里程碑补齐，
字段命名与 SRS 表 3-3-2 对齐。
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field

# 用户名规则（SRS）：4~32 位，字母、数字、下划线
USERNAME_PATTERN = r"^[A-Za-z0-9_]{4,32}$"


# --------------------------------------------------------------------------- #
# 认证（api/auth.py）
# --------------------------------------------------------------------------- #
class RegisterRequest(BaseModel):
    username: str = Field(pattern=USERNAME_PATTERN, description="4~32 位字母、数字或下划线")
    # 密码规则 SRS 未细化，先按 8~64 定，注册页同款提示
    password: str = Field(min_length=8, max_length=64, description="8~64 位")
    display_name: str | None = Field(default=None, max_length=64)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=1, max_length=64)


class TokenResponse(BaseModel):
    token: str
    expires_in: int  # 秒


# --------------------------------------------------------------------------- #
# 用户（api/users.py）
# --------------------------------------------------------------------------- #
class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    display_name: str | None = None
    target_position: str | None = None
    has_custom_llm: bool = False
    quota_date: date | None = None
    quota_used: int = 0


class UserProfileUpdate(BaseModel):
    """PUT /users/me：个人资料更新（自定义模型配置走独立接口，P5）。"""

    display_name: str | None = Field(default=None, max_length=64)
    target_position: str | None = Field(default=None, max_length=64)
