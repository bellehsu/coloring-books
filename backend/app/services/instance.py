"""DB 與 storage 的身分標記（storage_instance_id）。

同一個隨機 id 存在兩個地方：DB 的 `app_meta`（key `storage_instance_id`）與 storage 的物件
`books/_instance.json`（LocalStorage 只允許 books/ 與 cache/，所以放在 books/ 底下）。
- 標記一律直接讀寫遠端（CachedStorage 的 inner），不經過本機快取：快取裡可能是換 bucket 前的舊標記。
- 後端啟動時（`ensure_instance`）：兩邊都沒有時，只有「DB 至少有一本書」或「storage 的 books/ 是空的」
  才建立同一個 id（新部署、或既有部署第一次升級）。空的 DB 遇到 books/ 已有物件、沒有標記的 storage
  不自動認領：只記醒目的警告，兩邊都不寫（storage_gc 與 delete-data 會拒絕執行），由操作者用
  `scripts/storage_gc.py --init-instance` 確認後建立。只有一邊有或兩邊不同也只警告、照常服務。
- `scripts/storage_gc.py` 與 `scripts/invites.py delete-data` 執行前要求兩邊都有且相同（`require_instance`），
  否則什麼都不刪（DB 與 bucket 不是一組時，「沒有資料列指到」的物件其實是別人的活物件）。
- 這個物件永遠不能被刪：GC 不把它當孤兒，delete-data 只刪 `books/{book_id}/`。
"""

from __future__ import annotations

import json
import logging
import secrets
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.dialects.sqlite import insert
from sqlmodel import Session, col, delete, select

from app.models import AppMeta, Book
from app.storage import Storage, StorageNotFound

log = logging.getLogger(__name__)

MARKER_KEY = "books/_instance.json"
META_KEY = "storage_instance_id"


class InstanceMarkerError(ValueError):
    """storage 裡的身分標記內容壞掉（不是預期的 JSON）。"""


class InstanceUnavailable(RuntimeError):
    """讀寫 storage 的身分標記時發生「不存在」以外的錯誤（例如 R2 403、逾時、連不上）。"""


class InstanceMismatch(RuntimeError):
    """DB 與 storage 的身分標記不一致（或缺少、或壞掉）：不是一組，什麼都不能刪。"""


@dataclass(frozen=True)
class InstanceCheck:
    db_id: str | None
    bucket_id: str | None
    created: bool = False  # 這次啟動寫了標記（DB 或 storage）
    adoption_refused: bool = False  # 兩邊都沒有，但 DB 沒有書、storage 的 books/ 不是空的：不自動建立

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


def _remote(storage: Storage) -> Storage:
    """標記不經過本機快取：CachedStorage 直接用 inner（快取裡可能是另一個 bucket 的舊標記）。"""
    return getattr(storage, "inner", storage)


def storage_label(storage: Storage) -> str:
    """給人確認用的 storage 名稱：R2 是 bucket 名稱，本機是資料夾的絕對路徑。"""
    inner = _remote(storage)
    bucket = getattr(inner, "bucket", None)
    if bucket:
        return str(bucket)
    root = getattr(inner, "root", None)
    return str(Path(root).resolve()) if root is not None else repr(inner)


def bucket_instance_id(storage: Storage) -> str | None:
    """storage 的身分標記；沒有回 None，內容壞掉丟 InstanceMarkerError，其他錯誤丟 InstanceUnavailable。"""
    try:
        raw = _remote(storage).get(MARKER_KEY)
    except StorageNotFound:
        return None
    except Exception as e:  # noqa: BLE001  403、逾時、連不上：不能當成「沒有標記」
        raise InstanceUnavailable(f"讀取 storage 的 {MARKER_KEY} 失敗：{type(e).__name__}: {e}") from e
    try:
        value = json.loads(raw.decode("utf-8"))[META_KEY]
    except (UnicodeDecodeError, ValueError, KeyError, TypeError) as e:
        raise InstanceMarkerError(f"{MARKER_KEY} 內容無法解析") from e
    if not isinstance(value, str) or not value:
        raise InstanceMarkerError(f"{MARKER_KEY} 內容無法解析")
    return value


def write_bucket_instance_id(storage: Storage, value: str) -> None:
    try:
        _remote(storage).put(MARKER_KEY, json.dumps({META_KEY: value}).encode("utf-8"), "application/json")
    except Exception as e:  # noqa: BLE001
        raise InstanceUnavailable(f"寫入 storage 的 {MARKER_KEY} 失敗：{type(e).__name__}: {e}") from e


def storage_has_objects(storage: Storage) -> bool:
    """storage 的 books/ 底下有標記以外的物件嗎（一次 list）。"""
    try:
        return any(k != MARKER_KEY for k in _remote(storage).list("books/"))
    except Exception as e:  # noqa: BLE001
        raise InstanceUnavailable(f"列出 storage 的 books/ 失敗：{type(e).__name__}: {e}") from e


def db_has_books(session: Session) -> bool:
    return session.exec(select(Book.id).limit(1)).first() is not None


def check_instance(session: Session, storage: Storage) -> InstanceCheck:
    """只讀：兩邊的 id。標記壞掉丟 InstanceMarkerError，讀不到丟 InstanceUnavailable。"""
    return InstanceCheck(db_instance_id(session), bucket_instance_id(storage))


def create_instance(session: Session, storage: Storage) -> InstanceCheck:
    """兩邊都沒有時建立同一個 id，回報寫完之後重新讀到的真實狀態（不覆寫已存在的任何一邊）。

    兩個行程同時建立時，DB 以 INSERT OR IGNORE 決定贏家，大家都用 DB 裡那一個寫 storage；
    寫 storage 前再讀一次，已經有（別人的）標記就不寫；寫完再讀一次，被別人蓋掉就回報對不上。"""
    inserted = session.exec(
        insert(AppMeta).values(key=META_KEY, value=secrets.token_hex(16)).on_conflict_do_nothing()
        .returning(AppMeta.key)
    ).first() is not None
    session.commit()
    db_id = db_instance_id(session)
    wrote = False
    if bucket_instance_id(storage) is None:
        write_bucket_instance_id(storage, db_id)
        wrote = True
    return InstanceCheck(db_id, bucket_instance_id(storage), created=inserted or wrote)


def adopt_bucket_instance_id(session: Session, storage: Storage) -> InstanceCheck:
    """DB 沒有 id、storage 有標記時，把 storage 的 id 寫進 DB（不覆寫已存在的 DB id）。"""
    bucket_id = bucket_instance_id(storage)
    if bucket_id is None:
        return check_instance(session, storage)
    session.exec(insert(AppMeta).values(key=META_KEY, value=bucket_id).on_conflict_do_nothing())
    session.commit()
    return check_instance(session, storage)


def ensure_instance(session: Session, storage: Storage) -> InstanceCheck:
    """後端啟動時呼叫：兩邊都沒有，且 DB 有書或 storage 的 books/ 是空的，才建立同一個 id；其他情況只回報、不寫。"""
    res = check_instance(session, storage)
    if res.db_id is not None or res.bucket_id is not None:
        return res
    if not db_has_books(session) and storage_has_objects(storage):
        return InstanceCheck(None, None, adoption_refused=True)
    return create_instance(session, storage)


def mismatch_hint(res: InstanceCheck) -> str:
    """對不上時給操作者的下一步。"""
    if res.db_id and res.bucket_id is None:
        return ("storage 沒有身分標記：確認 paths.database 與 storage 設定是同一組後，"
                "用 scripts/storage_gc.py --init-instance 把資料庫的 id 寫進 storage。")
    if res.db_id is None and res.bucket_id:
        return ("資料庫沒有身分標記（例如降級後又升級，或還原了 0007 之前的備份）：確認設定指的是同一組後，"
                "用 scripts/storage_gc.py --init-instance --adopt-bucket-id 把 storage 的 id 寫進資料庫。")
    if res.db_id is None and res.bucket_id is None:
        return ("兩邊都沒有身分標記：後端啟動時只在資料庫至少有一本書、或 storage 的 books/ 是空的時候自動建立。"
                "確認設定指的是同一組後，用 scripts/storage_gc.py --init-instance 建立。")
    return "兩邊的 id 不同：資料庫與 storage（資料夾／bucket）不是一組，請檢查 paths.database 與 storage 設定。"


def require_instance(session: Session, storage: Storage, *, tool: str) -> InstanceCheck:
    """要刪東西之前：兩邊都有且相同才回傳；否則丟 InstanceMismatch（讀不到標記丟 InstanceUnavailable）。"""
    try:
        res = check_instance(session, storage)
    except InstanceMarkerError as e:
        raise InstanceMismatch(f"{e}；{tool} 不執行，什麼都沒刪。") from e
    if not res.ok:
        raise InstanceMismatch(
            f"資料庫與 storage 的身分標記不一致，{tool} 不執行，什麼都沒刪。\n{res.describe()}\n{mismatch_hint(res)}"
        )
    return res
