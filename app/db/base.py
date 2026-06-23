from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    # 所有 ORM 模型的共同基类，Alembic 也通过 Base.metadata 发现表结构。
    pass
