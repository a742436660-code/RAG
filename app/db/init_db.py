from app.db.base import Base
from app.db.fts import ensure_fts
from app.db.session import get_engine


def init_db() -> None:
    # create_all 负责普通 SQLAlchemy 表；ensure_fts 负责 SQLite FTS5 虚拟表。
    # Alembic 迁移也会创建这些表，但本地启动时自动初始化能降低试用门槛。
    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    ensure_fts(engine)
