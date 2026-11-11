"""共用相依：從 request.app.state 取得 session、storage、worker；目前的 owner 與擁有權檢查。"""

from collections.abc import Iterator

from fastapi import Depends, HTTPException, Request
from pydantic import TypeAdapter, ValidationError
from sqlmodel import Session, select

from app.models import Book, Generation, Owner, Page
from app.services import auth
from app.services.worker import GenerationWorker
from app.storage import Storage

SESSION_COOKIE = "cb_session"
NEED_INVITE = "請用邀請連結開啟"
LINK_REVOKED = "連結已失效"
BOOK_NOT_FOUND = "找不到這本繪本"
PAGE_NOT_FOUND = "找不到這一頁"
GENERATION_NOT_FOUND = "找不到這筆生成紀錄"


def get_session(request: Request) -> Iterator[Session]:
    with request.app.state.sessions() as session:
        yield session


def get_storage(request: Request) -> Storage:
    return request.app.state.storage


def get_worker(request: Request) -> GenerationWorker:
    return request.app.state.worker


def current_owner(request: Request, session: Session = Depends(get_session)) -> Owner:
    """每個請求都查 session 與 owner（撤銷立即生效）。

    - 有 cookie：查不到／過期 → 401「請用邀請連結開啟」（壞掉的 cookie 不會退回 dev_owner）；
    - 沒有 cookie：設定了 auth.dev_owner 就是那個 admin，否則 401「請用邀請連結開啟」；
    - owner 已撤銷 → 401「連結已失效」。
    """
    sid = request.cookies.get(SESSION_COOKIE)
    if sid:
        owner = auth.owner_for_session(session, sid)
    else:
        dev = request.app.state.settings.auth.dev_owner
        owner = auth.resolve_dev_owner(session, dev) if dev else None
    if owner is None:
        raise HTTPException(401, NEED_INVITE)
    if owner.revoked_at is not None:
        raise HTTPException(401, LINK_REVOKED)
    return owner


def require_admin(owner: Owner = Depends(current_owner)) -> Owner:
    if not owner.is_admin:
        raise HTTPException(403, "只有管理者可以使用")
    return owner


def owned_book(session: Session, owner: Owner, book_id: int) -> Book:
    """owner 的書（admin 可看全部）；別人的書與不存在一樣回 404，不透露它存在。"""
    book = session.get(Book, book_id)
    if book is None or not (owner.is_admin or book.owner_id == owner.id):
        raise HTTPException(404, BOOK_NOT_FOUND)
    return book


def owned_page(session: Session, owner: Owner, page_id: int) -> Page:
    """頁面的擁有者＝所屬書的擁有者。"""
    row = session.exec(
        select(Page, Book.owner_id).join(Book, Book.id == Page.book_id).where(Page.id == page_id)
    ).first()
    if row is None or not (owner.is_admin or row[1] == owner.id):
        raise HTTPException(404, PAGE_NOT_FOUND)
    return row[0]


def owned_generation(session: Session, owner: Owner, gen_id: int) -> Generation:
    """生成紀錄的擁有者＝所屬書的擁有者。"""
    row = session.exec(
        select(Generation, Book.owner_id)
        .join(Book, Book.id == Generation.book_id)
        .where(Generation.id == gen_id)
    ).first()
    if row is None or not (owner.is_admin or row[1] == owner.id):
        raise HTTPException(404, GENERATION_NOT_FOUND)
    return row[0]


_PATH_CHECKS = (
    ("book_id", owned_book, BOOK_NOT_FOUND),
    ("page_id", owned_page, PAGE_NOT_FOUND),
    ("gen_id", owned_generation, GENERATION_NOT_FOUND),
)
# 與 FastAPI 驗證路徑參數相同的轉換（寬鬆模式："1.0"、" 1"、"+1"、"01" 都是 1），
# 這裡檢查的 id 才會跟端點實際拿到的 id 一樣
_INT = TypeAdapter(int)


def require_owned_path(
    request: Request,
    owner: Owner = Depends(current_owner),
    session: Session = Depends(get_session),
) -> Owner:
    """路由層級的擁有權檢查：路徑裡的 book_id／page_id／gen_id 都要屬於目前的 owner。

    掛在每個 API router 上（main.py），在 body 驗證與端點本身之前執行：別人的 id 一律 404，
    什麼都不會被讀或改；新加的路由只要沿用這些參數名稱就自動受保護（tests/test_ownership.py 會檢查）。
    fail closed：id 轉不成整數也回 404（不交給後面處理）。端點本身也再以 owned_* 載入（縱深防禦）。
    """
    for name, check, not_found in _PATH_CHECKS:
        if name not in request.path_params:
            continue
        try:
            value = _INT.validate_python(request.path_params[name])
        except ValidationError:
            raise HTTPException(404, not_found) from None
        check(session, owner, value)
    return owner
