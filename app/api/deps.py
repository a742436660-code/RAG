from app.db.session import get_db

# API 层统一从这里导出依赖，后续如果要增加认证、租户或权限依赖，可以集中扩展。
__all__ = ["get_db"]
