"""邀請連結：GET /i/{token}（經 Next.js rewrite 與前端同源）。

token 有效 → 建立 session、設定 cb_session cookie（httpOnly、SameSite=Lax、Path=/、90 天），303 轉到 `/`；
不存在或已撤銷 → 303 轉到前端的 `/invalid-link`（不設 cookie）。
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session

from app.api.deps import SESSION_COOKIE, get_session
from app.services import auth

router = APIRouter(tags=["invites"])

INVALID_LINK_PATH = "/invalid-link"


# 不是給前端程式呼叫的 API（瀏覽器直接開），不放進 OpenAPI／產生的前端型別
@router.get("/i/{token}", response_class=RedirectResponse, status_code=303, include_in_schema=False)
def open_invite(token: str, request: Request, session: Session = Depends(get_session)) -> RedirectResponse:
    owner = auth.owner_for_token(session, token)
    if owner is None:
        return RedirectResponse(INVALID_LINK_PATH, status_code=303)
    sid = auth.start_session(session, owner)
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(
        SESSION_COOKIE,
        sid,
        max_age=int(auth.SESSION_LIFETIME.total_seconds()),
        path="/",
        httponly=True,
        samesite="lax",
        secure=request.app.state.settings.auth.cookie_secure,
    )
    return resp
