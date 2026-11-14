"""统一错误码与全局异常处理（Plan §7）。

所有接口错误一律以 JSON `{"code": <int>, "message": <str>, "detail": <any>}` 返回：

  * `AppError`          —— 业务代码主动抛出，自带错误码 + HTTP 状态码
  * `RequestValidationError` —— pydantic 请求参数校验失败，归入 40000 / HTTP 400
  * 未捕获异常           —— 500 兜底（50000），内部细节只进日志不回给客户端

错误码取值见下方常量；新增错误码先在 Plan §7 的表里登记再使用。
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logger import get_logger

log = get_logger(__name__)

# --------------------------------------------------------------------------- #
# 错误码（SRS 规定 + Plan §7 补充）
# --------------------------------------------------------------------------- #
PARAM_INVALID = 40000  # 请求参数校验失败
RESUME_FORMAT_UNSUPPORTED = 40001  # 简历文件格式不支持
RESUME_TOO_LARGE = 40002  # 简历文件超过大小上限
RESUME_NO_TEXT = 40003  # PDF 无文字层，抽不出文本
UNAUTHORIZED = 40101  # 未登录 / token 过期或无效 / 用户名密码错误
FORBIDDEN = 40301  # 访问他人资源
NOT_FOUND = 40401  # 资源不存在
GENERATING = 40901  # 上一轮回答仍在生成中
USERNAME_TAKEN = 40902  # 用户名已被占用
RATE_LIMIT = 42901  # 请求过于频繁（每分钟次数限制）
QUOTA_EXCEEDED = 42902  # 每日免费面试额度已用完
REPO_INACCESSIBLE = 42201  # 仓库不存在或私有
STATE_INVALID = 42202  # 当前面试状态不允许该操作
UPSTREAM_TIMEOUT = 50401  # 外部服务（LLM / GitHub / Tavily）超时
INTERNAL = 50000  # 服务器内部错误（兜底）


class AppError(Exception):
    """业务异常：接口层与业务层通用的「错误码 + 人话 + 详情」。"""

    def __init__(
        self,
        code: int = PARAM_INVALID,
        message: str = "请求处理失败",
        detail: Any = None,
        status: int = 400,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail
        self.status = status


def _error_body(code: int, message: str, detail: Any = None) -> dict[str, Any]:
    body: dict[str, Any] = {"code": code, "message": message}
    if detail is not None:
        body["detail"] = detail
    return body


def register_exception_handlers(app: FastAPI) -> None:
    """在 FastAPI 实例上挂全局异常处理器（main.create_app 里调用）。"""

    @app.exception_handler(AppError)
    async def on_app_error(_req: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status,
            content=_error_body(exc.code, exc.message, exc.detail),
        )

    @app.exception_handler(RequestValidationError)
    async def on_validation_error(_req: Request, exc: RequestValidationError) -> JSONResponse:
        # 取第一条校验错误拼成「字段 + 原因」，其余进 detail
        errors = exc.errors()
        first = errors[0] if errors else {}
        loc = ".".join(str(p) for p in first.get("loc", ()) if p != "body")
        msg = first.get("msg", "参数不合法")
        message = f"参数校验失败：{loc} {msg}" if loc else f"参数校验失败：{msg}"
        return JSONResponse(
            status_code=400,
            content=_error_body(PARAM_INVALID, message, errors[:5]),
        )

    @app.exception_handler(StarletteHTTPException)
    async def on_http_exception(_req: Request, exc: StarletteHTTPException) -> JSONResponse:
        # 例如路由未命中（404）、方法不允许（405）——也按统一错误格式返回
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(
                NOT_FOUND if exc.status_code == 404 else PARAM_INVALID,
                str(exc.detail),
            ),
        )

    @app.exception_handler(Exception)
    async def on_unhandled(req: Request, exc: Exception) -> JSONResponse:
        # 兜底：内部细节只写日志（含堆栈），不外泄给客户端
        log.exception("未处理异常 %s %s", req.method, req.url.path)
        return JSONResponse(
            status_code=500,
            content=_error_body(INTERNAL, "服务器内部错误，请稍后重试"),
        )
