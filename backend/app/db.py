"""SQLite engine 與 session 輔助函式。"""

import os
from collections.abc import Callable
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, inspect
from sqlmodel import Session, create_engine

from app import models  # noqa: F401  確保資料表已註冊到 metadata


# 其他連線持有寫入鎖時最多等幾秒（pysqlite 預設 5 秒）。寫入交易都很短（storage I/O 一律在交易外），
# 這只是保險：同時有幾個寫入者排隊時不要立刻 database is locked
SQLITE_BUSY_TIMEOUT_S = 30


def make_engine(database: Path) -> Engine:
    # worker 執行緒與 API 執行緒共用同一個 engine，所以關閉 check_same_thread
    return create_engine(
        f"sqlite:///{database}",
        connect_args={"check_same_thread": False, "timeout": SQLITE_BUSY_TIMEOUT_S},
    )


BASELINE_REVISION = "0001"  # Phase 2 以 create_all 建立的結構
_ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"


def alembic_config(engine: Engine) -> Config:
    cfg = Config(str(_ALEMBIC_INI))
    cfg.attributes["engine"] = engine  # env.py 在沒有 connection 時使用
    return cfg


class LegacyUpgradeRefused(RuntimeError):
    """舊（0006 之前）而且已有書的資料庫：不默默升級（升級會改寫、無法完整還原）。"""


ALLOW_LEGACY_ENV = "APP_ALLOW_LEGACY_UPGRADE"
_FIRST_OWNER_REVISION = 6  # 0006 起才有 owner；更早的是 Phase 9 以前的資料庫


def _refuse_legacy_upgrade(conn) -> None:
    """資料庫低於 0006 且至少有一本書時拒絕升級，除非環境變數 APP_ALLOW_LEGACY_UPGRADE=1。
    只讀；在任何寫入之前呼叫，所以拒絕時檔案完全不變。"""
    if os.environ.get(ALLOW_LEGACY_ENV) == "1":
        return
    tables = set(inspect(conn).get_table_names())
    if "book" not in tables:
        return
    current = MigrationContext.configure(conn).get_current_revision() if "alembic_version" in tables else BASELINE_REVISION
    if current is None or not current.isdigit() or int(current) >= _FIRST_OWNER_REVISION:
        return
    if conn.exec_driver_sql("SELECT count(*) FROM book").scalar() == 0:
        return
    raise LegacyUpgradeRefused(
        f"資料庫停在舊版本（{current}），而且已經有繪本；升級會改寫它而且無法完整還原，所以不自動升級。"
        "如果這是 Phase 9 的舊資料庫，請看 README「從 Phase 9 升級」：先備份 storage/，並把 paths.database／paths.storage_dir "
        "改指到新的位置（腳本也要載入 scripts/dev.env 的設定）。確定要就地升級這個資料庫時，"
        f"設定環境變數 {ALLOW_LEGACY_ENV}=1 再執行。"
    )


def init_db(engine: Engine) -> None:
    """以 Alembic 升級到最新；Phase 2 舊庫（有 book 表但無 alembic_version）先標記為 baseline。
    0006 之前而且已有書的舊庫會丟 LegacyUpgradeRefused（見 _refuse_legacy_upgrade）。"""
    cfg = alembic_config(engine)
    with engine.connect() as conn:
        _refuse_legacy_upgrade(conn)
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        tables = set(inspect(conn).get_table_names())
        if "book" in tables and "alembic_version" not in tables:
            command.stamp(cfg, BASELINE_REVISION)
        command.upgrade(cfg, "head")


def schema_revisions(engine: Engine) -> tuple[str | None, str | None]:
    """(資料庫目前的 alembic revision, 最新的 head)；只讀，不升級。沒有 alembic_version 時目前是 None。"""
    head = ScriptDirectory.from_config(alembic_config(engine)).get_current_head()
    with engine.connect() as conn:
        current = MigrationContext.configure(conn).get_current_revision()
    return current, head


def session_factory(engine: Engine) -> Callable[[], Session]:
    def make() -> Session:
        return Session(engine, expire_on_commit=False)

    return make
