"""Alembic 環境：以 SQLModel.metadata 為目標；資料庫位置來自 settings。"""

from alembic import context
from sqlalchemy import create_engine
from sqlmodel import SQLModel

import app.models  # noqa: F401  註冊資料表到 metadata

config = context.config
target_metadata = SQLModel.metadata


def _run(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:  # 程式內呼叫（init_db）：沿用傳入的連線
        _run(connection)
        return
    engine = config.attributes.get("engine")
    if engine is not None:
        with engine.begin() as conn:
            _run(conn)
        return
    from app.config import load_settings

    engine = create_engine(f"sqlite:///{load_settings().paths.database}")
    with engine.connect() as conn:
        _run(conn)
    engine.dispose()


run_migrations_online()
