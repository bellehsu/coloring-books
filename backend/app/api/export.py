"""匯出端點：書本 PDF、匯出摘要、單頁列印 PNG。同步 `def`（CPU 工作走 threadpool）。"""

from typing import Literal
from urllib.parse import quote

import numpy as np
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from PIL import Image
from sqlmodel import Session, col, select

from app.api.books import _book_or_404
from app.api.deps import get_session, get_storage
from app.api.pages import _page_or_404
from app.models import Book, Page, PageStatus
from app.services.export import build_pdf, compose_page, png_bytes
from app.services.layout import compute_layout
from app.services.page_lineart import LineartUnavailable, ensure_page_lineart
from app.storage import Storage

router = APIRouter(prefix="/api", tags=["export"])

NOTHING_TO_EXPORT = "沒有可匯出的頁面（需要先通過 QA）"


def _ordered_pages(session: Session, book_id: int) -> list[Page]:
    return list(session.exec(
        select(Page).where(col(Page.book_id) == book_id).order_by(col(Page.position), col(Page.id))
    ).all())


def _composed(session: Session, storage: Storage, book: Book, page: Page):
    layout = compute_layout(book.paper_size, book.print_mode)
    try:
        lin = ensure_page_lineart(session, storage, page.id)
    except LineartUnavailable as e:
        raise HTTPException(409, f"第 {page.position + 1} 頁：{e.message}") from e
    with Image.open(storage.abs(lin.path)) as im:
        ink = np.asarray(im.convert("L")) < 128
    return compose_page(ink, layout)


@router.get("/books/{book_id}/export/summary")
def export_summary(
    book_id: int,
    session: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
) -> dict:
    _book_or_404(session, book_id)
    pages = _ordered_pages(session, book_id)
    exportable, skipped = [], []
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
    return {"total": len(pages), "passed": len(exportable),
            "exportable_ids": exportable, "skipped": skipped}


@router.get("/books/{book_id}/export.pdf")
def export_pdf(
    book_id: int,
    include: Literal["passed", "all"] = "passed",
    session: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
) -> Response:
    book = _book_or_404(session, book_id)
    candidates = [
        p for p in _ordered_pages(session, book_id)
        if p.selected_generation_id is not None
        and (include == "all" or p.status == PageStatus.PASSED)
    ]
    images = []
    for p in candidates:
        img = _composed(session, storage, book, p)
        # 重算線稿可能把這一頁退回草稿（例如剛換紙張大小）；只匯出「現在」仍通過的頁面
        session.refresh(p)
        if include == "passed" and p.status != PageStatus.PASSED:
            continue
        images.append(img)
    if not images:
        raise HTTPException(409, NOTHING_TO_EXPORT)
    pdf = build_pdf(images)
    disp = f'attachment; filename="book-{book_id}.pdf"; filename*=UTF-8\'\'{quote(book.title)}.pdf'
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": disp})


@router.get("/pages/{page_id}/print.png")
def print_png(
    page_id: int,
    session: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
) -> Response:
    page = _page_or_404(session, page_id)
    if page.selected_generation_id is None:
        raise HTTPException(409, "這一頁還沒有選定圖片")
    book = _book_or_404(session, page.book_id)
    return Response(content=png_bytes(_composed(session, storage, book, page)),
                    media_type="image/png")
