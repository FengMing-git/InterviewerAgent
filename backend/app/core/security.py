"""安全原语（Plan §3：core/security.py）。

三件事：
  * 密码：bcrypt（存 60 字符 hash，接口永远不接收/不回传明文之外的形态）；
  * 登录态：PyJWT HS256（payload 只放 sub=用户 id + iat/exp，不放敏感信息）；
  * 自定义模型 API Key：AES-256-GCM（密文含认证标签 + 12 字节随机 nonce 成对存库）。
"""

from __future__ import annotations

import base64
import os
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt as pyjwt
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import JWT_EXPIRE_HOURS, settings
from app.core.errors import INTERNAL, UNAUTHORIZED, AppError

_JWT_ALGORITHM = "HS256"


# --------------------------------------------------------------------------- #
# 密码（bcrypt）
# --------------------------------------------------------------------------- #
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii"))
    except ValueError:
        # 库里的 hash 损坏（长度/字符集不对）——视为校验失败，不暴露细节
        return False


# --------------------------------------------------------------------------- #
# JWT
# --------------------------------------------------------------------------- #
def create_access_token(user_id: int) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),  # PyJWT 2.10+ 要求 sub 是字符串
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=JWT_EXPIRE_HOURS)).timestamp()),
    }
    return pyjwt.encode(payload, settings.jwt_secret, algorithm=_JWT_ALGORITHM)


def decode_access_token(token: str) -> int:
    """校验签名与有效期，返回用户 id；失败抛 40101。"""
    try:
        payload = pyjwt.decode(token, settings.jwt_secret, algorithms=[_JWT_ALGORITHM])
        return int(payload["sub"])
    except pyjwt.ExpiredSignatureError:
        raise AppError(UNAUTHORIZED, "登录已过期，请重新登录", status=401) from None
    except (pyjwt.InvalidTokenError, KeyError, TypeError, ValueError):
        raise AppError(UNAUTHORIZED, "无效的访问令牌", status=401) from None


# --------------------------------------------------------------------------- #
# 自定义模型 API Key（AES-256-GCM）
# --------------------------------------------------------------------------- #
def _encryption_key() -> bytes:
    key = base64.b64decode(settings.app_encryption_key)
    if len(key) != 32:
        raise AppError(
            INTERNAL,
            "APP_ENCRYPTION_KEY 配置错误：必须是 32 字节密钥的 base64",
            status=500,
        )
    return key


def encrypt_secret(plaintext: str) -> tuple[bytes, bytes]:
    """返回 (cipher, nonce)。cipher 已含 GCM 认证标签，两者成对存库。"""
    nonce = os.urandom(12)
    cipher = AESGCM(_encryption_key()).encrypt(nonce, plaintext.encode("utf-8"), None)
    return cipher, nonce


def decrypt_secret(cipher: bytes, nonce: bytes) -> str:
    """解密自定义 API Key；密文被篡改/密钥不符时抛 InvalidTag（调用方兜底）。"""
    return AESGCM(_encryption_key()).decrypt(nonce, cipher, None).decode("utf-8")
