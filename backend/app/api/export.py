"""匯出端點：書本 PDF、匯出摘要、單頁列印 PNG。同步 `def`（CPU 工作走 threadpool）。"""

import io
from pathlib import Path
from typing import Literal
from urllib.parse import quote

import numpy as np
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from PIL import Image
from sqlmodel import Session, col, select

from app.api.deps import current_owner, get_caption_font, get_session, get_storage, owned_book, owned_page
from app.models import Book, Owner, Page, PageStatus
from app.services.captions import CAPTION_FONT_MISSING, CaptionFontMissing
from app.services.export import build_pdf, compose_page, png_bytes
from app.services.layout import compute_layout
from app.services.page_lineart import LineartUnavailable, ensure_page_lineart, read_page_lineart
from app.storage import Storage

router = APIRouter(prefix="/api", tags=["export"])

NOTHING_TO_EXPORT = "沒有可匯出的頁面（需要先通過 QA）"


def _ordered_pages(session: Session, book_id: int) -> list[Page]:
    return list(session.exec(
        select(Page).where(col(Page.book_id) == book_id).order_by(col(Page.position), col(Page.id))
    ).all())


def composed_page(session: Session, storage: Storage, book: Book, page: Page, font_path: Path | None):
    """列印頁的唯一出口（擁有者 PDF、print.png、分享圖、分享 PDF）：線稿＋頁面文字。字型不能用 → 409。"""
    layout = compute_layout(book.paper_size, book.print_mode)
    try:
        _lin, data = read_page_lineart(session, storage, page.id)
    except LineartUnavailable as e:
        raise HTTPException(409, f"第 {page.position + 1} 頁：{e.message}") from e
    with Image.open(io.BytesIO(data)) as im:
        ink = np.asarray(im.convert("L")) < 128
    try:
        return compose_page(ink, layout, (page.caption_zh, page.caption_en), font_path,
                            show=(page.caption_show_zh, page.caption_show_en))
    except CaptionFontMissing as e:
        raise HTTPException(409, CAPTION_FONT_MISSING) from e


Include = Literal["passed", "ready", "all"]


def _included(p: Page, include: Include) -> bool:
    """passed：通過的頁；ready：通過而且沒有「請看一下」（B 的可以印，spec §4.3）；all：有選定圖的頁。"""
    if p.selected_generation_id is None:
        return False
    if include == "all":
        return True
    return p.status == PageStatus.PASSED and (include == "passed" or p.attention is None)


def book_pdf(session: Session, storage: Storage, book: Book, include: Include,
             font_path: Path | None) -> bytes:
    """書的 PDF（擁有者匯出與分享下載共用）：include=passed 只含「現在」仍通過的頁面，ready 再去掉
    「請看一下」的頁（B 的「印出這本書」預設）；沒有頁面 → 409。"""
    candidates = [p for p in _ordered_pages(session, book.id) if _included(p, include)]
    images = []
    for p in candidates:
        img = composed_page(session, storage, book, p, font_path)
        # 重算線稿可能把這一頁退回草稿（例如剛換紙張大小）；只匯出「現在」仍符合的頁面
        session.refresh(p)
        if not _included(p, include):
            continue
        images.append(img)
    if not images:
        raise HTTPException(409, NOTHING_TO_EXPORT)
    return build_pdf(images)


def pdf_response(book: Book, pdf: bytes) -> Response:
    disp = f'attachment; filename="book-{book.id}.pdf"; filename*=UTF-8\'\'{quote(book.title)}.pdf'
    return Response(content=pdf, media_type="application/pdf", headers={"Content-Disposition": disp})


@router.get("/books/{book_id}/export/summary")
def export_summary(
    book_id: int,
    session: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
    owner: Owner = Depends(current_owner),
) -> dict:
    owned_book(session, owner, book_id)
    pages = _ordered_pages(session, book_id)
    exportable, ready, skipped = [], [], []
    for p in pages:
        if p.selected_generation_id is not None and p.status == PageStatus.PASSED:
            # 先確保線稿是最新的（可能因此退回草稿），再依目前狀態計數
            try:
                ensure_page_lineart(session, storage, p.id)
            except LineartUnavailable:
                pass
            session.refresh(p)
        if p.selected_generation_id is None:
            skipped.append({"page_id": p.id, "position": p.position, "reason": "no_selection"})
        elif p.status != PageStatus.PASSED:
            skipped.append({"page_id": p.id, "position": p.position, "reason": "not_passed"})
        else:
            exportable.append(p.id)
            if p.attention is None:
                ready.append(p.id)
    # ready_ids：include=ready 會印的頁（通過、沒有「請看一下」），B 的「先看一眼」用
    return {"total": len(pages), "passed": len(exportable),
            "exportable_ids": exportable, "ready_ids": ready, "skipped": skipped}


@router.get("/books/{book_id}/export.pdf")
def export_pdf(
    book_id: int,
    include: Include = "passed",
    session: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
    owner: Owner = Depends(current_owner),
    font_path: Path | None = Depends(get_caption_font),
) -> Response:
    book = owned_book(session, owner, book_id)
    pdf = book_pdf(session, storage, book, include, font_path)
    return pdf_response(book, pdf)


@router.get("/pages/{page_id}/print.png")
def print_png(
    page_id: int,
    session: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
    owner: Owner = Depends(current_owner),
    font_path: Path | None = Depends(get_caption_font),
) -> Response:
    page = owned_page(session, owner, page_id)
    if page.selected_generation_id is None:
        raise HTTPException(409, "這一頁還沒有選定圖片")
    book = owned_book(session, owner, page.book_id)
    return Response(content=png_bytes(composed_page(session, storage, book, page, font_path)),
                    media_type="image/png")
