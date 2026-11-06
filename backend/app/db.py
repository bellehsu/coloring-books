"""SQLite engine 與 session 輔助函式。"""

from collections.abc import Callable
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, inspect
from sqlmodel import Session, create_engine

from app import models  # noqa: F401  確保資料表已註冊到 metadata


def make_engine(database: Path) -> Engine:
    # worker 執行緒與 API 執行緒共用同一個 engine，所以關閉 check_same_thread
    return create_engine(f"sqlite:///{database}", connect_args={"check_same_thread": False})


BASELINE_REVISION = "0001"  # Phase 2 以 create_all 建立的結構
_ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"


def alembic_config(engine: Engine) -> Config:
    cfg = Config(str(_ALEMBIC_INI))
    cfg.attributes["engine"] = engine  # env.py 在沒有 connection 時使用
    return cfg


def init_db(engine: Engine) -> None:
    """以 Alembic 升級到最新；Phase 2 舊庫（有 book 表但無 alembic_version）先標記為 baseline。"""
    cfg = alembic_config(engine)
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        tables = set(inspect(conn).get_table_names())
        if "book" in tables and "alembic_version" not in tables:
            command.stamp(cfg, BASELINE_REVISION)
        command.upgrade(cfg, "head")


def session_factory(engine: Engine) -> Callable[[], Session]:
    def make() -> Session:
        return Session(engine, expire_on_commit=False)

    return make
