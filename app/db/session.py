from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings


def _ensure_sqlite_parent(database_url: str) -> None:
    # SQLite 使用文件路径时，先确保父目录存在，否则 create_engine 后首次连接会失败。
    url = make_url(database_url)
    database = url.database
    if not url.drivername.startswith("sqlite") or not database or database == ":memory:":
        return
    Path(database).parent.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_engine() -> Engine:
    settings = get_settings()
    _ensure_sqlite_parent(settings.database_url)
    # SQLite 默认不允许连接跨线程使用；FastAPI/TestClient 下需要关闭该限制。
    connect_args = (
        {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
    )
    engine = create_engine(settings.database_url, connect_args=connect_args, future=True)

    if settings.database_url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
            # WAL 提升读写并发体验；foreign_keys 开启级联删除；
            # busy_timeout 避免短时间写锁冲突时立刻失败。
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

    return engine


@lru_cache
def get_sessionmaker() -> sessionmaker[Session]:
    # expire_on_commit=False 让提交后仍可读取 ORM 对象属性，服务层返回响应更方便。
    return sessionmaker(
        bind=get_engine(), autoflush=False, autocommit=False, expire_on_commit=False
    )


def get_db() -> Iterator[Session]:
    # FastAPI 依赖：每个请求创建一个 Session，请求结束后关闭。
    db = get_sessionmaker()()
    try:
        yield db
    finally:
        db.close()


def reset_database_state() -> None:
    # 测试辅助：清掉缓存的 engine/sessionmaker，确保环境变量切换后生效。
    get_sessionmaker.cache_clear()
    get_engine.cache_clear()
