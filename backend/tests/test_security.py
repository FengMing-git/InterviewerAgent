"""安全原语测试（P0）：bcrypt / JWT / AES-256-GCM。

Plan §10 的 test_security 在 P5 会补「访问他人资源返回 403」等接口级用例；
这里先覆盖三个纯函数的往返与失败路径。依赖 .env（jwt_secret / app_encryption_key），
配置缺失时整体 skip。
"""

from __future__ import annotations

import time

import jwt as pyjwt
import pytest

from app.config import settings

try:
    from app.core.errors import UNAUTHORIZED, AppError
    from app.core.security import (
        create_access_token,
        decode_access_token,
        decrypt_secret,
        encrypt_secret,
        hash_password,
        verify_password,
    )

    _CONFIG_OK = True
except Exception:
    _CONFIG_OK = False

pytestmark = pytest.mark.skipif(
    not _CONFIG_OK, reason="backend/.env 缺失或必填项不全（含 jwt_secret / app_encryption_key）"
)


def test_password_hash_roundtrip():
    h = hash_password("s3cret-passw0rd")
    assert h != "s3cret-passw0rd"
    assert verify_password("s3cret-passw0rd", h)
    assert not verify_password("wrong-password", h)
    # hash 里带盐：两次结果不同
    assert hash_password("s3cret-passw0rd") != h


def test_jwt_roundtrip():
    token = create_access_token(42)
    assert decode_access_token(token) == 42


def test_jwt_expired():
    payload = {"sub": "42", "exp": int(time.time()) - 10, "iat": int(time.time()) - 100}
    expired = pyjwt.encode(payload, settings.jwt_secret, algorithm="HS256")
    with pytest.raises(AppError) as excinfo:
        decode_access_token(expired)
    assert excinfo.value.code == UNAUTHORIZED


def test_jwt_garbage():
    for token in ("", "not.a.jwt", "x.y.z"):
        with pytest.raises(AppError) as excinfo:
            decode_access_token(token)
        assert excinfo.value.code == UNAUTHORIZED


def test_jwt_wrong_signature():
    payload = {"sub": "42", "exp": int(time.time()) + 60}
    forged = pyjwt.encode(payload, "another-secret-key", algorithm="HS256")
    with pytest.raises(AppError):
        decode_access_token(forged)


def test_aes_gcm_roundtrip():
    cipher, nonce = encrypt_secret("sk-my-custom-key-123")
    assert b"sk-my-custom-key-123" not in cipher  # 看不到明文
    assert decrypt_secret(cipher, nonce) == "sk-my-custom-key-123"


def test_aes_gcm_tamper_detected():
    cipher, nonce = encrypt_secret("sk-my-custom-key-123")
    with pytest.raises(Exception):  # InvalidTag
        decrypt_secret(cipher[:-4] + b"\x00\x01\x02\x03", nonce)
    # nonce 换一个也不行
    with pytest.raises(Exception):
        decrypt_secret(cipher, b"\x00" * 12)
