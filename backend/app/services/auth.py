"""邀請連結與 session（Phase 10）：DB 只存 token／session id 的 sha256。

- 一個邀請連結＝一個 owner。`create_owner` 回傳的原始 token 只會出現這一次。
- `/i/{token}` 驗證 token 後 `start_session` 產生隨機 session id 放進 cookie；
  每個請求都以 session id 的 hash 查 owner_session 與 owner，撤銷立即生效。
- 本機開發（`auth.dev_owner`）：沒有 cookie 的請求視為該名稱的 admin owner（`resolve_dev_owner`）。
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import case
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, delete, select

from app.models import Owner, OwnerSession, utcnow

SESSION_LIFETIME = timedelta(days=90)
# last_seen 只是參考資訊；每個請求都寫入會增加 SQLite 寫入競爭，所以至多每小時更新一次
LAST_SEEN_RESOLUTION = timedelta(hours=1)
# dev owner 的 token_hash 用這個前綴：不是 64 位 hex，任何 token 的 sha256 都不會等於它（無法用連結登入）；
# token_hash 唯一，所以同時建立 dev owner 時只有一筆寫得進去
_DEV_TOKEN_PREFIX = "dev-owner:"


def hash_token(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def create_owner(session: Session, name: str, *, is_admin: bool = False, cap: int = 60) -> tuple[Owner, str]:
    """建立邀請（owner），回傳 (owner, 原始 token)。原始 token 不寫進 DB。"""
    token = secrets.token_urlsafe(32)
    owner = Owner(name=name, token_hash=hash_token(token), is_admin=is_admin, daily_generation_cap=cap)
    session.add(owner)
    session.commit()
    session.refresh(owner)
    return owner, token


def rotate_token(session: Session, owner: Owner) -> str:
    """換發 owner 的邀請 token（舊連結立即失效）並刪掉它所有 session，回傳新的原始 token。不 commit。"""
    token = secrets.token_urlsafe(32)
    owner.token_hash = hash_token(token)
    session.add(owner)
    delete_sessions(session, owner.id)
    return token


def delete_sessions(session: Session, owner_id: int) -> int:
    """刪掉 owner 的所有 session（已開過連結的瀏覽器下一個請求就是 401）。回傳筆數。不 commit。"""
    res = session.exec(delete(OwnerSession).where(col(OwnerSession.owner_id) == owner_id))
    return res.rowcount


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
