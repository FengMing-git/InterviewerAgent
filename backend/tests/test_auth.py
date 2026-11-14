"""认证接口测试（P0）：注册 / 登录 / token 鉴权闭环。

数据库不可用时经 conftest 的 client fixture 整体 skip。
"""

from __future__ import annotations


def _register(client, username="alice", password="password123", display_name=None):
    return client.post(
        "/api/v1/auth/register",
        json={"username": username, "password": password, "display_name": display_name},
    )


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_register_returns_token(client):
    r = _register(client, display_name="Alice")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["token"]
    assert body["expires_in"] > 0


def test_register_duplicate_username(client):
    _register(client)
    r = _register(client)  # 同名再来一次
    assert r.status_code == 409
    assert r.json()["code"] == 40902


def test_register_validation_error(client):
    r = _register(client, username="ab", password="password123")  # 用户名太短
    assert r.status_code == 400
    assert r.json()["code"] == 40000
    r = _register(client, username="bob", password="short")  # 密码太短
    assert r.status_code == 400
    assert r.json()["code"] == 40000


def test_login_success_and_wrong_password(client):
    _register(client, username="carol", password="password123")

    ok = client.post(
        "/api/v1/auth/login", json={"username": "carol", "password": "password123"}
    )
    assert ok.status_code == 200
    assert ok.json()["token"]

    bad = client.post(
        "/api/v1/auth/login", json={"username": "carol", "password": "wrong-pass"}
    )
    assert bad.status_code == 401
    assert bad.json()["code"] == 40101

    # 用户不存在：与密码错误同口径，避免账号枚举
    ghost = client.post(
        "/api/v1/auth/login", json={"username": "ghost404", "password": "whatever1"}
    )
    assert ghost.status_code == 401
    assert ghost.json()["code"] == 40101


def test_me_requires_valid_token(client):
    _register(client, username="dave", password="password123")
    token = client.post(
        "/api/v1/auth/login", json={"username": "dave", "password": "password123"}
    ).json()["token"]

    # 无 token
    r = client.get("/api/v1/users/me")
    assert r.status_code == 401
    assert r.json()["code"] == 40101

    # 伪造 token
    r = client.get("/api/v1/users/me", headers=_auth_header("not-a-jwt"))
    assert r.status_code == 401
    assert r.json()["code"] == 40101

    # 正确 token
    r = client.get("/api/v1/users/me", headers=_auth_header(token))
    assert r.status_code == 200
    body = r.json()
    assert body["username"] == "dave"
    assert body["has_custom_llm"] is False


def test_update_profile(client):
    _register(client, username="erin", password="password123")
    token = client.post(
        "/api/v1/auth/login", json={"username": "erin", "password": "password123"}
    ).json()["token"]

    r = client.put(
        "/api/v1/users/me",
        json={"display_name": "小 E", "target_position": "后端开发"},
        headers=_auth_header(token),
    )
    assert r.status_code == 200
    assert r.json()["display_name"] == "小 E"
    assert r.json()["target_position"] == "后端开发"

    # 部分更新：只改一个字段，另一个保持
    r = client.put(
        "/api/v1/users/me", json={"display_name": "Erin"}, headers=_auth_header(token)
    )
    assert r.status_code == 200
    body = r.json()
    assert body["display_name"] == "Erin"
    assert body["target_position"] == "后端开发"
