"""邀請連結與 session（Phase 10）：DB 只存 token／session id 的 sha256。

- 一個邀請連結＝一個 owner。`create_owner` 回傳的原始 token 只會出現這一次。
- `/i/{token}` 驗證 token 後 `start_session` 產生隨機 session id 放進 cookie；
  每個請求都以 session id 的 hash 查 owner_session 與 owner，撤銷立即生效。
- 本機開發（`auth.dev_owner`）：沒有 cookie 的請求視為該名稱的 admin owner（`resolve_dev_owner`）。
"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import case, func
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, delete, select, update

from app.config import Settings, SettingsError
from app.models import Book, Generation, GenStatus, Owner, OwnerSession, Page, PageTask, PageTaskStatus, utcnow
from app.services.usage import refund_generations
from app.storage import load_or_create_key_file

SESSION_LIFETIME = timedelta(days=90)
# last_seen 只是參考資訊；每個請求都寫入會增加 SQLite 寫入競爭，所以至多每小時更新一次
LAST_SEEN_RESOLUTION = timedelta(hours=1)
# dev owner 的 token_hash 用這個前綴：不是 64 位 hex，任何 token 的 sha256 都不會等於它（無法用連結登入）；
# token_hash 唯一，所以同時建立 dev owner 時只有一筆寫得進去
_DEV_TOKEN_PREFIX = "dev-owner:"


def hash_token(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


log = logging.getLogger("app.auth")
LINK_KEY_FILE = ".link_key"
DUPLICATE_NAME = "這個名字已經有邀請了"
_warned_keys: set[str] = set()


class DuplicateInviteName(ValueError):
    """已經有同名而且未撤銷的邀請。"""


def link_key(settings: Settings) -> bytes:
    """加密 owner.token_encrypted 的金鑰：env APP_AUTH__LINK_KEY，否則 <paths.storage_dir>/.link_key（0600，自動產生）。
    與簽章金鑰（storage.signing_key）同樣的做法：每次呼叫都重讀，沒有行程內快取，重啟後自然是同一把。"""
    configured = settings.auth.link_key
    if configured is not None and configured.get_secret_value():
        return configured.get_secret_value().encode()
    return load_or_create_key_file(settings.paths.storage_dir, LINK_KEY_FILE, "連結金鑰")


def safe_link_key(settings: Settings) -> bytes | None:
    """link_key，但金鑰檔壞掉（不完整、讀不了）時回 None 並記一次警告：連結就只是「無法顯示」，不讓 API 500。"""
    try:
        return link_key(settings)
    except (SettingsError, OSError, ValueError) as e:
        msg = str(e)
        if msg not in _warned_keys:
            _warned_keys.add(msg)
            log.warning("連結金鑰無法使用，邀請連結都會顯示為「無法再顯示」：%s", msg)
        return None


def _fernet(key: bytes) -> Fernet:
    # 任意長度的秘密字串都可以當金鑰：取 sha256 轉成 Fernet 要的 32 bytes url-safe base64
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(key).digest()))


def encrypt_token(token: str, key: bytes) -> str:
    return _fernet(key).encrypt(token.encode("utf-8")).decode("ascii")


def decrypt_token(encrypted: str | None, key: bytes) -> str | None:
    """解不開（NULL、金鑰換了、資料壞掉）回 None，不丟例外。"""
    if not encrypted:
        return None
    try:
        return _fernet(key).decrypt(encrypted.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        return None


def reveal_link(owner: Owner, key: bytes | None, base_url: str) -> str | None:
    """邀請連結 `<base_url>/i/<token>`；已撤銷、舊資料（沒有加密的 token）或金鑰不對時回 None。"""
    if owner.revoked_at is not None or key is None:
        return None
    token = decrypt_token(owner.token_encrypted, key)
    return None if token is None else f"{base_url.rstrip('/')}/i/{token}"


def create_owner(
    session: Session, name: str, *, is_admin: bool = False, cap: int = 60, link_key: bytes | None = None
) -> tuple[Owner, str]:
    """建立邀請（owner），回傳 (owner, 原始 token)。原始 token 不以明文寫進 DB：
    DB 存 sha256（查詢用）；有給 link_key 時另存加密版（管理頁再顯示連結用），沒給就是 NULL。"""
    token = secrets.token_urlsafe(32)
    owner = Owner(
        name=name, token_hash=hash_token(token), is_admin=is_admin, daily_generation_cap=cap,
        token_encrypted=encrypt_token(token, link_key) if link_key else None,
    )
    session.add(owner)
    # 同名檢查與寫入在同一個交易：INSERT 取得 SQLite 的寫入鎖後才數，同時建立同名邀請的另一方會等到這個交易
    # 結束，再數時就看得到我們這一筆，所以只有一個會成功
    session.flush()
    active = session.exec(
        select(func.count()).select_from(Owner).where(col(Owner.name) == name, col(Owner.revoked_at).is_(None))
    ).one()
    if active > 1:
        session.rollback()
        raise DuplicateInviteName(DUPLICATE_NAME)
    session.commit()
    session.refresh(owner)
    return owner, token


def rotate_token(session: Session, owner: Owner, link_key: bytes | None = None) -> str:
    """換發 owner 的邀請 token（舊連結立即失效）並刪掉它所有 session，回傳新的原始 token。不 commit。
    加密版跟著換（沒給 link_key 就清成 NULL：舊的加密 token 已經不是有效連結）。"""
    token = secrets.token_urlsafe(32)
    owner.token_hash = hash_token(token)
    owner.token_encrypted = encrypt_token(token, link_key) if link_key else None
    session.add(owner)
    delete_sessions(session, owner.id)
    return token


def delete_sessions(session: Session, owner_id: int) -> int:
    """刪掉 owner 的所有 session（已開過連結的瀏覽器下一個請求就是 401）。回傳筆數。不 commit。"""
    res = session.exec(delete(OwnerSession).where(col(OwnerSession.owner_id) == owner_id))
    return res.rowcount


def cancel_queued_in_books(session: Session, book_ids, *, reason: str) -> int:
    """取消這些書裡還在排隊的工作（book_ids 是 SQL 的 select(Book.id)…）：QUEUED generation → CANCELLED（退還額度）、
    QUEUED 的 PageTask → FAILED（PageTask 沒有 cancelled 狀態），error 都是 reason。RUNNING 的不動。
    回傳取消的 generation 數。條件式 UPDATE 會取得寫入鎖，worker 之後無法再認領這些工作。
    不 commit（呼叫端與主要變更同一個交易）。"""
    owned = book_ids
    cancelled = list(session.exec(
        update(Generation)
        .where(col(Generation.book_id).in_(owned), col(Generation.status) == GenStatus.QUEUED)
        .values(status=GenStatus.CANCELLED, error=reason, finished_at=utcnow())
        .returning(Generation.id)
    ).scalars().all())
    refund_generations(session, cancelled)  # 替別人的書排隊的 admin 也拿回額度
    session.exec(
        update(PageTask)
        .where(
            col(PageTask.page_id).in_(select(Page.id).where(col(Page.book_id).in_(owned))),
            col(PageTask.status) == PageTaskStatus.QUEUED.value,
        )
        .values(status=PageTaskStatus.FAILED.value, error=reason, updated_at=utcnow())
    )
    return len(cancelled)


def cancel_owner_queue(session: Session, owner_id: int, *, reason: str) -> int:
    """取消 owner 的書裡還在排隊的工作（RUNNING 的不動，讓它跑完）；回傳取消的 generation 數。不 commit。"""
    return cancel_queued_in_books(session, select(Book.id).where(col(Book.owner_id) == owner_id), reason=reason)


def cancel_revoked_queues(session: Session) -> int:
    """已撤銷的 owner 若還有排隊中的工作（撤銷時 RUNNING 的後來被放回佇列，或成功後排了頁面流程），
    取消掉並 commit；回傳取消的 generation 數。worker 在認領工作之前呼叫，所以撤銷後不會再有這個 owner 的新工作開始。"""
    owned = select(Book.id).where(col(Book.owner_id) == Owner.id)
    queued_gen = select(Generation.id).where(
        col(Generation.book_id).in_(owned), col(Generation.status) == GenStatus.QUEUED
    ).exists()
    queued_task = select(PageTask.id).where(
        col(PageTask.page_id).in_(select(Page.id).where(col(Page.book_id).in_(owned))),
        col(PageTask.status) == PageTaskStatus.QUEUED.value,
    ).exists()
    ids = session.exec(
        select(Owner.id).where(col(Owner.revoked_at).is_not(None), queued_gen | queued_task)
    ).all()
    n = sum(cancel_owner_queue(session, oid, reason="邀請已撤銷") for oid in ids)
    if ids:
        session.commit()
    return n


def owner_for_token(session: Session, token: str) -> Owner | None:
    """邀請 token 對應的 owner；不存在或已撤銷回 None。"""
    owner = session.exec(select(Owner).where(Owner.token_hash == hash_token(token))).first()
    if owner is None or owner.revoked_at is not None:
        return None
    return owner


def start_session(session: Session, owner: Owner) -> str:
    """為 owner 建立新 session，回傳要放進 cookie 的原始 session id（DB 只存 hash）。"""
    sid = secrets.token_urlsafe(32)
    session.add(OwnerSession(owner_id=owner.id, session_hash=hash_token(sid)))
    session.commit()
    return sid


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def owner_for_session(session: Session, sid: str) -> Owner | None:
    """cookie 的 session id 對應的 owner；不存在、已過期（90 天）或 owner 已刪除回 None。
    已撤銷的 owner 照樣回傳，由呼叫端回「連結已失效」。"""
    row = session.exec(select(OwnerSession).where(OwnerSession.session_hash == hash_token(sid))).first()
    if row is None:
        return None
    now = utcnow()
    if now - _aware(row.created_at) > SESSION_LIFETIME:
        session.delete(row)
        session.commit()
        return None
    owner = session.get(Owner, row.owner_id)
    if owner is None:
        return None
    if now - _aware(row.last_seen) > LAST_SEEN_RESOLUTION:
        row.last_seen = now
        session.add(row)
        session.commit()
    return owner


def _find_dev_owner(session: Session, name: str) -> Owner | None:
    """沿用既有的 admin：同名者優先，其次未撤銷者，再依 id。"""
    return session.exec(
        select(Owner)
        .where(col(Owner.is_admin))
        .order_by(
            case((col(Owner.name) == name, 0), else_=1),
            case((col(Owner.revoked_at).is_(None), 0), else_=1),
            col(Owner.id),
        )
    ).first()


def resolve_dev_owner(session: Session, name: str) -> Owner:
    """本機開發用的 admin owner：已有 admin 就沿用（例如遷移時建立、沒人知道 token 的 admin），
    沒有才建立一個（name＝dev_owner）。同時建立時靠 token_hash 唯一鍵，輸的一方回頭拿贏家那一列。"""
    owner = _find_dev_owner(session, name)
    if owner is not None:
        return owner
    owner = Owner(name=name, token_hash=_DEV_TOKEN_PREFIX + name, is_admin=True)
    session.add(owner)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        owner = _find_dev_owner(session, name)
        if owner is None:  # 不可能：唯一鍵衝突代表那一列已存在
            raise
        return owner
    session.refresh(owner)
    return owner
