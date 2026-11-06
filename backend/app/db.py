"""SQLite engine 與 session 輔助函式。"""

from collections.abc import Callable
from pathlib import Path

from sqlalchemy import Engine
from sqlmodel import Session, SQLModel, create_engine

from app import models  # noqa: F401  確保資料表已註冊到 metadata


def make_engine(database: Path) -> Engine:
    # worker 執行緒與 API 執行緒共用同一個 engine，所以關閉 check_same_thread
    return create_engine(f"sqlite:///{database}", connect_args={"check_same_thread": False})


def init_db(engine: Engine) -> None:
    SQLModel.metadata.create_all(engine)


def session_factory(engine: Engine) -> Callable[[], Session]:
    def make() -> Session:
        return Session(engine, expire_on_commit=False)

    return make
