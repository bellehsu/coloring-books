"""分享的繪本（唯讀）：/api/shared/...。

任何有效、未撤銷的 session 都能看別人分享的書，但只有完成（PASSED）的頁面，且看不到任何編輯用的資料。
這個 router 不掛 require_owned_path（那是擁有權檢查），但每個端點仍要 current_owner。
書沒分享、擁有者已撤銷、頁面未完成、或不存在一律同一個 404，不透露它存在。
檢視與下載不計入每日生成上限（不會呼叫 usage.reserve）。
"""

import hashlib
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from PIL import Image
from sqlmodel import Session, col, select

from app.api.deps import current_owner, get_caption_font, get_session, get_storage, parse_db_id
from app.api.export import book_pdf, composed_page, pdf_response
from app.api.pages import page_outs
from app.api.schemas import SharedBookDetailOut, SharedBookOut, SharedPageOut
from app.models import Book, Owner, Page, PageStatus
from app.services.export import png_bytes
from app.storage import Storage


def _bounded_ids(request: Request, _owner: Owner = Depends(current_owner)) -> None:
    """路徑裡的 book_id／page_id 超出 SQLite INTEGER 範圍（或轉不成整數）：不可能存在，直接 404（不然查詢丟 OverflowError → 500）。"""
    for name in ("book_id", "page_id"):
        if name not in request.path_params:
            continue
        parse_db_id(request.path_params[name], SHARED_NOT_FOUND)


router = APIRouter(prefix="/api/shared", tags=["shared"], dependencies=[Depends(_bounded_ids)])

SHARED_NOT_FOUND = "找不到這本分享的繪本"
IMAGE_LONG_EDGE = 1600
IMAGE_CACHE = "private, max-age=300"


def _image_urls(session: Session, pages: list[Page]) -> dict[int, str]:
    """分享圖網址帶 ?v=（圖有 5 分鐘私人快取）：目前線稿 key＋文字＋要印哪幾行，跟擁有者的 print.png 預覽同一組輸入。"""
    urls = {}
    for p, out in zip(pages, page_outs(session, pages)):
        raw = "|".join((out.lineart_url or "", p.caption_zh or "", p.caption_en or "",
                        f"{int(p.caption_show_zh)}{int(p.caption_show_en)}"))
        v = hashlib.sha256(raw.encode()).hexdigest()[:16]
        urls[p.id] = f"/api/shared/pages/{p.id}/image.png?v={v}"
    return urls


def _gone() -> HTTPException:
    return HTTPException(404, SHARED_NOT_FOUND)


def _visible_books(session: Session, book_id: int | None = None) -> list[tuple[Book, Owner, list[Page]]]:
    """分享中、擁有者未撤銷、至少一個完成頁的書（含完成頁，依位置排序）。"""
    stmt = (
        select(Book, Owner)
        .join(Owner, Owner.id == Book.owner_id)
        .where(col(Book.shared_at).is_not(None), col(Owner.revoked_at).is_(None))
    )
    if book_id is not None:
        stmt = stmt.where(col(Book.id) == book_id)
    rows = session.exec(stmt.order_by(col(Book.shared_at).desc(), col(Book.id).desc())).all()
    if not rows:
        return []
    passed: dict[int, list[Page]] = {b.id: [] for b, _ in rows}
    for p in session.exec(
        select(Page)
        .where(col(Page.book_id).in_(list(passed)), col(Page.status) == PageStatus.PASSED,
               col(Page.selected_generation_id).is_not(None))
        .order_by(col(Page.position), col(Page.id))
    ).all():
        passed[p.book_id].append(p)
    return [(b, o, passed[b.id]) for b, o in rows if passed[b.id]]


def _visible_book(session: Session, book_id: int) -> tuple[Book, Owner, list[Page]]:
    found = _visible_books(session, book_id)
    if not found:
        raise _gone()
    return found[0]


@router.get("/books", response_model=list[SharedBookOut])
def list_shared_books(
    session: Session = Depends(get_session), owner: Owner = Depends(current_owner)
) -> list[SharedBookOut]:
    """別人分享的書（自己的不列，首頁用徽章標示），分享時間新到舊。"""
    books = [(b, o, pages) for b, o, pages in _visible_books(session) if b.owner_id != owner.id]
    covers = _image_urls(session, [pages[0] for _b, _o, pages in books])
    return [
        SharedBookOut(id=b.id, title=b.title, author=o.name, paper_size=b.paper_size, page_count=len(pages),
                      cover_image_url=covers[pages[0].id], shared_at=b.shared_at,
                      mine=False)
        for b, o, pages in books
    ]


@router.get("/books/{book_id}", response_model=SharedBookDetailOut)
def shared_book(
    book_id: int, session: Session = Depends(get_session), owner: Owner = Depends(current_owner)
) -> SharedBookDetailOut:
    b, o, pages = _visible_book(session, book_id)
    urls = _image_urls(session, pages)
    return SharedBookDetailOut(
        id=b.id, title=b.title, author=o.name, paper_size=b.paper_size, mine=b.owner_id == owner.id,
        pages=[SharedPageOut(id=p.id, position=p.position, image_url=urls[p.id]) for p in pages],
    )


@router.get("/pages/{page_id}/image.png")
def shared_page_image(
    page_id: int,
    session: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
    owner: Owner = Depends(current_owner),
    font_path: Path | None = Depends(get_caption_font),
) -> Response:
    """PDF 用的同一張合成頁（含修線），長邊縮到 1600 px 以內。"""
    # 刻意不分擁有者載入：這裡只是先找出頁面所屬的書。真正的可見性檢查是下一行的 _visible_book
    # （書要分享中、擁有者未撤銷）與其後的「這一頁在完成頁清單裡」；沒分享的書或未完成的頁一律同一個 404。
    # （tests/test_sharing.py::test_shared_image_of_unshared_book_page_is_404）
    page = session.exec(select(Page).where(col(Page.id) == page_id)).first()
    if page is None:
        raise _gone()
    book, _o, pages = _visible_book(session, page.book_id)
    if page.id not in {p.id for p in pages}:
        raise _gone()
    try:
        img = composed_page(session, storage, book, page, font_path)
    except HTTPException:
        raise _gone() from None  # 線稿讀不到：對檢視者就是沒有這一頁
    session.refresh(page)
    if page.status != PageStatus.PASSED:  # 重算線稿把它退回草稿了
        raise _gone()
    small = img.convert("L")
    small.thumbnail((IMAGE_LONG_EDGE, IMAGE_LONG_EDGE), Image.Resampling.LANCZOS)
    return Response(content=png_bytes(small), media_type="image/png", headers={"Cache-Control": IMAGE_CACHE})


@router.get("/books/{book_id}/export.pdf")
def shared_book_pdf(
    book_id: int,
    session: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
    owner: Owner = Depends(current_owner),
    font_path: Path | None = Depends(get_caption_font),
) -> Response:
    book, _o, _pages = _visible_book(session, book_id)
    try:
        pdf = book_pdf(session, storage, book, "passed", font_path, skip_unrenderable=True)
    except HTTPException:
        raise _gone() from None
    return pdf_response(book, pdf)
