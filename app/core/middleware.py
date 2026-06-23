from contextvars import ContextVar
from typing import Optional
from uuid import uuid4

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

request_id_var: ContextVar[Optional[str]] = ContextVar("request_id", default=None)


class RequestIdMiddleware(BaseHTTPMiddleware):
    # request_id 存在 ContextVar 中，服务层和日志格式化器无需显式传参也能读取。
    async def dispatch(self, request: Request, call_next) -> Response:
        # 如果调用方传了 X-Request-ID，就沿用；否则生成新的 UUID。
        request_id = request.headers.get("X-Request-ID") or str(uuid4())
        token = request_id_var.set(request_id)
        try:
            response = await call_next(request)
        finally:
            # 请求结束后恢复 ContextVar，避免异步环境下串到其他请求。
            request_id_var.reset(token)
        response.headers["X-Request-ID"] = request_id
        return response
