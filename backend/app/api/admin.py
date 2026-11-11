"""管理者的邀請管理（spec §4.10）：GET /api/me、GET/POST /api/admin/invites。

- /api/admin/* 只有管理者能用，其他人（含沒有管理權限的有效 session）一律 404，不透露路由存在；沒登入照常 401。
- 建立邀請沿用 services/auth.create_owner（與 scripts/invites.py 同一份邏輯）；原始 token 以 Fernet 加密後存在
  owner.token_encrypted，列表時解開組成連結。換發、撤銷、改上限、刪資料仍用 scripts/invites.py。
- 連結前綴：auth.invite_base_url，否則請求的 origin（X-Forwarded-Host／Proto 優先：Next 的 rewrite 會改寫 Host）。
"""

import re

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func
from sqlmodel import Session, col, select

from app.api.deps import current_owner, get_session, require_admin_hidden
from app.api.schemas import InviteCreate, InviteOut, MeOut
from app.models import Book, Owner, OwnerSession, Usage
from app.services import auth
from app.services.usage import clock, taipei_day

me_router = APIRouter(prefix="/api", tags=["me"])
router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin_hidden)])

DUPLICATE_NAME = "這個名字已經有邀請了"
# 轉發過來的主機名稱：host 或 host:port（含 IPv6 的 [::1]:3000）；其他一律忽略，不讓怪字串進到連結裡
_HOST = re.compile(r"^(?:[A-Za-z0-9.-]+|\[[0-9A-Fa-f:.]+\])(?::\d{1,5})?$")


@me_router.get("/me", response_model=MeOut)
def me(owner: Owner = Depends(current_owner)) -> MeOut:
    return MeOut(name=owner.name, is_admin=owner.is_admin)


def _first(value: str | None) -> str:
    return (value or "").split(",")[0].strip()


def link_prefix(request: Request) -> str:
    """連結前綴（不含結尾 /）：設定的 auth.invite_base_url，否則瀏覽器實際開的 origin。"""
    configured = request.app.state.settings.auth.invite_base_url
    if configured and configured.strip():
        return configured.strip().rstrip("/")
    host = _first(request.headers.get("x-forwarded-host"))
    if not _HOST.match(host):
        host = _first(request.headers.get("host"))
        if not _HOST.match(host):
            host = request.url.netloc
    proto = _first(request.headers.get("x-forwarded-proto")).lower()
    if proto not in ("http", "https"):
        proto = request.url.scheme
    return f"{proto}://{host}"


def _rows(session: Session, owners: list[Owner], key: bytes, prefix: str) -> list[InviteOut]:
    ids = [o.id for o in owners]
    today = taipei_day(clock())
    used = dict(session.exec(
        select(Usage.owner_id, Usage.generations).where(col(Usage.day) == today, col(Usage.owner_id).in_(ids))
    ).all())
    books = dict(session.exec(
        select(Book.owner_id, func.count()).where(col(Book.owner_id).in_(ids)).group_by(col(Book.owner_id))
    ).all())
    seen = dict(session.exec(
        select(OwnerSession.owner_id, func.max(OwnerSession.last_seen))
        .where(col(OwnerSession.owner_id).in_(ids)).group_by(col(OwnerSession.owner_id))
    ).all())
    return [
        InviteOut(
            id=o.id, name=o.name, is_admin=o.is_admin,
            daily_generation_cap=None if o.is_admin else o.daily_generation_cap,
            today=used.get(o.id, 0), book_count=books.get(o.id, 0), last_seen=seen.get(o.id),
            created_at=o.created_at, revoked=o.revoked_at is not None,
            link=auth.reveal_link(o, key, prefix),
        )
        for o in owners
    ]


@router.get("/invites", response_model=list[InviteOut])
def list_invites(request: Request, session: Session = Depends(get_session)) -> list[InviteOut]:
    owners = list(session.exec(select(Owner).order_by(col(Owner.created_at).desc(), col(Owner.id).desc())).all())
    if not owners:
        return []
    return _rows(session, owners, auth.link_key(request.app.state.settings), link_prefix(request))


@router.post("/invites", response_model=InviteOut, status_code=201)
def create_invite(body: InviteCreate, request: Request, session: Session = Depends(get_session)) -> InviteOut:
    taken = session.exec(
        select(Owner.id).where(col(Owner.name) == body.name, col(Owner.revoked_at).is_(None))
    ).first()
    if taken is not None:
        raise HTTPException(409, DUPLICATE_NAME)
    key = auth.link_key(request.app.state.settings)
    owner, _token = auth.create_owner(
        session, body.name, is_admin=body.is_admin, cap=body.daily_generation_cap, link_key=key
    )
    return _rows(session, [owner], key, link_prefix(request))[0]
