"""健康检查测试（P0）：/health 配置与数据库探测。"""

from __future__ import annotations


def test_health_ok(client):
    """conftest 已确保 .env 配好 + 测试库可连，两项都应为 true。"""
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body == {"status": "ok", "config_ok": True, "db_ok": True}


def test_unknown_api_route_404_format(client):
    """未命中的 API 路径返回统一错误格式（code/message），而不是裸 HTML。"""
    r = client.get("/api/v1/no-such-endpoint")
    assert r.status_code == 404
    body = r.json()
    assert set(body) >= {"code", "message"}
    assert body["code"] == 40401
