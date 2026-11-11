"""共用相依：從 request.app.state 取得 session、storage、worker。"""

import hashlib
import secrets
from collections.abc import Iterator

from fastapi import Request
from sqlmodel import Session, col, select

from app.models import Owner

from app.services.worker import GenerationWorker
from app.storage import Storage


def get_session(request: Request) -> Iterator[Session]:
    with request.app.state.sessions() as session:
        yield session


def get_storage(request: Request) -> Storage:
    return request.app.state.storage


def get_worker(request: Request) -> GenerationWorker:
    return request.app.state.worker


def temporary_admin_owner(session: Session) -> Owner:
    """TEMPORARY（Phase 10 Task 4）：Task 5 以 current_owner 取代，屆時刪除此函式。

    回傳唯一的 admin owner；沒有就建立一個（name="admin"、隨機 token 的 hash，沒有人知道原始 token）。
    """
    owner = session.exec(select(Owner).where(col(Owner.is_admin)).order_by(col(Owner.id))).first()
    if owner is None:
        token_hash = hashlib.sha256(secrets.token_urlsafe(32).encode()).hexdigest()
        owner = Owner(name="admin", token_hash=token_hash, is_admin=True)
        session.add(owner)
        session.commit()
    return owner
