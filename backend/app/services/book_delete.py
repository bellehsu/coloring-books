"""刪除繪本的資料列（fix round 2026-11-13 #1）。物件由呼叫端在交易提交後刪。"""

from sqlmodel import Session, col, delete, select

from app.models import Book, Generation, LineartEdit, Page, PageHistory, PageTask
from app.services.auth import cancel_queued_in_books

BOOK_DELETED = "繪本已刪除"


def delete_books_rows(session: Session, book_ids: list[int]) -> None:
    """刪除這些書與底下的頁面、生成紀錄、修線、頁面流程、歷史；排隊中的工作先取消（退額度）。不 commit。
    順序：LineartEdit → PageTask → PageHistory → Generation → Page → Book（外鍵由子到父）。"""
    cancel_queued_in_books(session, select(Book.id).where(col(Book.id).in_(book_ids)), reason=BOOK_DELETED)
    page_ids = select(Page.id).where(col(Page.book_id).in_(book_ids))
    gen_ids = select(Generation.id).where(col(Generation.book_id).in_(book_ids))
    session.exec(delete(LineartEdit).where(
        col(LineartEdit.page_id).in_(page_ids) | col(LineartEdit.generation_id).in_(gen_ids)
    ))
    session.exec(delete(PageTask).where(col(PageTask.page_id).in_(page_ids)))
    session.exec(delete(PageHistory).where(col(PageHistory.page_id).in_(page_ids)))
    session.exec(delete(Generation).where(col(Generation.book_id).in_(book_ids)))
    session.exec(delete(Page).where(col(Page.book_id).in_(book_ids)))
    session.exec(delete(Book).where(col(Book.id).in_(book_ids)))
