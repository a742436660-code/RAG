import json
from typing import Any


def dumps_json(value: Any) -> str:
    # ensure_ascii=False 保留中文内容，separators 压缩存储体积。
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def loads_json(value: str, default: Any) -> Any:
    # 日志或消息中的 JSON 损坏时返回默认值，避免读取历史记录时整个接口失败。
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default
