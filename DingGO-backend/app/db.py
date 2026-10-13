from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


def make_engine(url: str):
    if url.startswith("sqlite:///"):
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
        eng = create_engine(url, connect_args={"check_same_thread": False})

        # SQLite 默认不检查外键，MySQL 检查：本地开发和测试也打开，否则「删除时还有别的表指着它」这类错误只会在服务器上暴露
        @event.listens_for(eng, "connect")
        def _fk_on(conn, _record):
            conn.execute("PRAGMA foreign_keys=ON")

        return eng
    return create_engine(url, pool_pre_ping=True, pool_recycle=3600)


engine = make_engine(get_settings().database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
