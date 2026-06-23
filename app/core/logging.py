import json
import logging
import sys
from datetime import datetime
from typing import Any

from app.core.middleware import request_id_var


class JsonFormatter(logging.Formatter):
    # 结构化日志便于本地 grep、容器日志采集和按 request_id 关联问题。
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.utcnow().isoformat(timespec="milliseconds") + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": request_id_var.get(None),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(debug: bool = False) -> None:
    # 每次创建 app 时重置 root handlers，避免测试中重复添加日志处理器。
    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(logging.DEBUG if debug else logging.INFO)
