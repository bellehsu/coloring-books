"""邀請連結：GET /i/{token}（經 Next.js rewrite 與前端同源）。

token 有效 → 建立 session、設定 cb_session cookie（httpOnly、SameSite=Lax、Path=/、90 天），303 轉到 `/`；
不存在或已撤銷 → 303 轉到前端的 `/invalid-link`（不設 cookie）。
瀏覽器已有「另一個 owner」的有效 session 時不直接覆蓋（跨站連結可以讓別人登入成自己的帳號）：
303 轉到 `/switch-account#<token>`，由那一頁問過使用者後 POST /api/invites/accept。
token 放在 fragment，不會送到伺服器也不會進日誌。
"""

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlmodel import Session

from app.models import Owner

from app.api.deps import SESSION_COOKIE, get_session
from app.services import auth

router = APIRouter(tags=["invites"])

INVALID_LINK_PATH = "/invalid-link"
SWITCH_ACCOUNT_PATH = "/switch-account"


# 不是給前端程式呼叫的 API（瀏覽器直接開），不放進 OpenAPI／產生的前端型別
def _set_session_cookie(resp: Response, request: Request, owner: Owner, session: Session) -> None:
    sid = auth.start_session(session, owner)
    resp.set_cookie(
        SESSION_COOKIE,
        sid,
        max_age=int(auth.SESSION_LIFETIME.total_seconds()),
        path="/",
        httponly=True,
        samesite="lax",
        secure=request.app.state.settings.auth.cookie_secure,
    )


def _current_session_owner(request: Request, session: Session) -> Owner | None:
    """cookie 對應的、未撤銷的 owner（跟 current_owner 的 cookie 查法相同；dev_owner 不算 session）。"""
    sid = request.cookies.get(SESSION_COOKIE)
    owner = auth.owner_for_session(session, sid) if sid else None
    return owner if owner is not None and owner.revoked_at is None else None


@router.get("/i/{token}", response_class=RedirectResponse, status_code=303, include_in_schema=False)
def open_invite(token: str, request: Request, session: Session = Depends(get_session)) -> RedirectResponse:
    owner = auth.owner_for_token(session, token)
    if owner is None:
        return RedirectResponse(INVALID_LINK_PATH, status_code=303)
    current = _current_session_owner(request, session)
    if current is not None and current.id != owner.id:
        return RedirectResponse(f"{SWITCH_ACCOUNT_PATH}#{token}", status_code=303)
    resp = RedirectResponse("/", status_code=303)
    _set_session_cookie(resp, request, owner, session)
    return resp


class InviteName(BaseModel):
    name: str


class AcceptBody(BaseModel):
    token: str


NOT_FOUND = "這個連結已經失效"


@router.get("/api/invites/peek", response_model=InviteName)
def peek_invite(x_invite_token: str | None = Header(None), session: Session = Depends(get_session)) -> InviteName:
    """切換身分確認頁用：這個邀請 token 是誰的。token 放 header（不進網址與日誌）；壞的或已撤銷 404。"""
    owner = auth.owner_for_token(session, x_invite_token) if x_invite_token else None
    if owner is None:
        raise HTTPException(404, NOT_FOUND)
    return InviteName(name=owner.name)


@router.post("/api/invites/accept", response_model=InviteName)
def accept_invite(
    body: AcceptBody, request: Request, response: Response, session: Session = Depends(get_session)
) -> InviteName:
    """使用者在確認頁按「切換」：效果同 GET /i/{token}（設 cookie），但是同源 POST。壞的或已撤銷 404。"""
    owner = auth.owner_for_token(session, body.token)
    if owner is None:
        raise HTTPException(404, NOT_FOUND)
    _set_session_cookie(response, request, owner, session)
    return InviteName(name=owner.name)
