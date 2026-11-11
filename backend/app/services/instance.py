"""DB 與 storage 的身分標記（storage_instance_id）。

同一個隨機 id 存在兩個地方：DB 的 `app_meta`（key `storage_instance_id`）與 storage 的物件
`books/_instance.json`（LocalStorage 只允許 books/ 與 cache/，所以放在 books/ 底下）。
- 後端啟動時（`ensure_instance`）：兩邊都沒有＝新部署，建立同一個 id；只有一邊有或兩邊不同，
  只記下醒目的警告、照常服務，不自動修正。
- `scripts/storage_gc.py` 執行前要求兩邊都有且相同，否則什麼都不刪（DB 與 bucket 不是一組時，
  「沒有資料列指到」的物件其實是別人的活物件）。
- 這個物件永遠不能被刪：GC 不把它當孤兒，delete-data 只刪 `books/{book_id}/`。
"""

from __future__ import annotations

import json
import logging
import secrets
from dataclasses import dataclass

from sqlalchemy.dialects.sqlite import insert
from sqlmodel import Session, col, delete

from app.models import AppMeta
from app.storage import Storage, StorageNotFound

log = logging.getLogger(__name__)

MARKER_KEY = "books/_instance.json"
META_KEY = "storage_instance_id"


class InstanceMarkerError(ValueError):
    """storage 裡的身分標記內容壞掉（不是預期的 JSON）。"""


@dataclass(frozen=True)
class InstanceCheck:
    db_id: str | None
    bucket_id: str | None
    created: bool = False

    @property
    def ok(self) -> bool:
        return self.db_id is not None and self.db_id == self.bucket_id

    def describe(self) -> str:
        return f"資料庫 storage_instance_id＝{self.db_id or '（沒有）'}；storage 的 {MARKER_KEY}＝{self.bucket_id or '（沒有）'}"


def db_instance_id(session: Session) -> str | None:
    row = session.get(AppMeta, META_KEY)
    return row.value if row is not None else None


def set_db_instance_id(session: Session, value: str) -> None:
    row = session.get(AppMeta, META_KEY) or AppMeta(key=META_KEY, value=value)
    row.value = value
    session.add(row)
    session.commit()


def delete_db_instance_id(session: Session) -> None:
    session.exec(delete(AppMeta).where(col(AppMeta.key) == META_KEY))
    session.commit()


def bucket_instance_id(storage: Storage) -> str | None:
    """storage 的身分標記；沒有回 None，內容壞掉丟 InstanceMarkerError。"""
    try:
        raw = storage.get(MARKER_KEY)
    except StorageNotFound:
        return None
    try:
        value = json.loads(raw.decode("utf-8"))[META_KEY]
    except (UnicodeDecodeError, ValueError, KeyError, TypeError) as e:
        raise InstanceMarkerError(f"{MARKER_KEY} 內容無法解析") from e
    if not isinstance(value, str) or not value:
        raise InstanceMarkerError(f"{MARKER_KEY} 內容無法解析")
    return value


def write_bucket_instance_id(storage: Storage, value: str) -> None:
    storage.put(MARKER_KEY, json.dumps({META_KEY: value}).encode("utf-8"), "application/json")


def check_instance(session: Session, storage: Storage) -> InstanceCheck:
    """只讀：兩邊的 id。標記壞掉丟 InstanceMarkerError。"""
    return InstanceCheck(db_instance_id(session), bucket_instance_id(storage))


def ensure_instance(session: Session, storage: Storage) -> InstanceCheck:
    """後端啟動時呼叫：兩邊都沒有就建立同一個 id；其他情況只回報、不寫。

    兩個行程同時建立時，DB 以 INSERT OR IGNORE 決定贏家，大家都用 DB 裡那一個寫 storage。"""
    res = check_instance(session, storage)
    if res.db_id is not None or res.bucket_id is not None:
        return res
    session.exec(insert(AppMeta).values(key=META_KEY, value=secrets.token_hex(16)).on_conflict_do_nothing())
    session.commit()
    db_id = db_instance_id(session)
    write_bucket_instance_id(storage, db_id)
    return InstanceCheck(db_id, db_id, created=True)
