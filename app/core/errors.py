from typing import Any, Optional


class AppError(Exception):
    # 服务层抛出的标准业务异常，最终由 main.py 中的 handler 转成 JSON 响应。
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(message)
