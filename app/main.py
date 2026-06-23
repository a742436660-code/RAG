import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.routes import router
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.logging import configure_logging
from app.core.middleware import RequestIdMiddleware, request_id_var
from app.db.init_db import init_db

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    # 应用工厂：所有运行环境（uvicorn、测试、Docker）都会从这里创建 FastAPI 实例。
    # 这里集中挂载中间件、异常处理器和路由，避免业务模块在 import 时产生副作用。
    settings = get_settings()
    configure_logging(debug=settings.debug)
    app = FastAPI(title=settings.app_name, debug=settings.debug)
    # 为每个请求生成或透传 X-Request-ID，后续日志和检索日志都可以用它串联一次请求。
    app.add_middleware(RequestIdMiddleware)
    # 统一把业务异常和参数校验异常转换成稳定的 JSON 响应格式。
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.include_router(router)

    @app.on_event("startup")
    def on_startup() -> None:
        # 本地 MVP 启动时会自动 create_all 并确保 FTS5 表存在。
        # 生产环境仍可使用 Alembic 管理 schema 演进。
        init_db()
        logger.info("application_started")

    return app


async def app_error_handler(request: Request, exc: Exception) -> JSONResponse:
    # AppError 是服务层主动抛出的“可预期业务错误”，例如文档不存在、文件重复。
    # 这里保留 code/details/request_id，方便前端展示和排查。
    if not isinstance(exc, AppError):
        raise exc
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "code": exc.code,
            "message": exc.message,
            "details": exc.details,
            "request_id": request_id_var.get(None),
        },
    )


async def validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    # Pydantic/FastAPI 的参数校验错误也统一包装，避免接口错误格式不一致。
    if not isinstance(exc, RequestValidationError):
        raise exc
    return JSONResponse(
        status_code=422,
        content={
            "code": "validation_error",
            "message": "Request validation failed.",
            "details": {"errors": exc.errors()},
            "request_id": request_id_var.get(None),
        },
    )


app = create_app()
