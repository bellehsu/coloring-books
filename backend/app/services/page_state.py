"""頁面的製作狀態（B 的每頁狀態與書卡統計）：製作中＞請看一下＞可以印＞還沒做完。"""

from __future__ import annotations

from typing import Literal

from sqlmodel import Session, col, select

from app.models import Generation, GenKind, GenStatus, Page, PageStatus, PageTask, PageTaskStatus
from app.services.slots import not_upload

GEN_ACTIVE = (GenStatus.QUEUED, GenStatus.RUNNING)
TASK_ACTIVE = (PageTaskStatus.QUEUED.value, PageTaskStatus.RUNNING.value)

PageState = Literal["ready", "attention", "working", "todo"]


def busy_page_ids(session: Session, book_ids: list[int]) -> set[int]:
    """這些書裡「製作中」的頁：有排隊中／執行中的生成（不含合成圖、上傳的背景），或頁面流程。
    與 book_overview 的 working 一致（首頁書卡的統計用）。"""
    if not book_ids:
        return set()
    busy = set(session.exec(
        select(Generation.page_id).where(
            col(Generation.book_id).in_(book_ids),
            col(Generation.status).in_(GEN_ACTIVE),
            col(Generation.page_id).is_not(None),
            col(Generation.kind) != GenKind.COMPOSITE,
            not_upload(),
        )
    ).all())
    busy.update(session.exec(
        select(PageTask.page_id).join(Page, col(Page.id) == col(PageTask.page_id)).where(
            col(Page.book_id).in_(book_ids), col(PageTask.status).in_(TASK_ACTIVE),
        )
    ).all())
    return busy


def page_state(page: Page, busy: set[int]) -> PageState:
    """B 的每頁狀態：製作中＞請看一下＞可以印＞還沒做完。線沒接起來不算請看一下（只是提示）。"""
    if page.id in busy:
        return "working"
    if page.attention:
        return "attention"
    if page.status == PageStatus.PASSED:
        return "ready"
    return "todo"
