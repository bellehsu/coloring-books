"""頁面的製作狀態（B 的每頁狀態與書卡統計）：製作中＞請看一下＞可以印＞還沒做完。"""

from __future__ import annotations

from typing import Literal

from sqlmodel import Session, col, select

from app.models import Generation, GenKind, GenStatus, Page, PageStatus, PageTask, PageTaskStatus
from app.services.slots import not_upload

GEN_ACTIVE = (GenStatus.QUEUED, GenStatus.RUNNING)
TASK_ACTIVE = (PageTaskStatus.QUEUED.value, PageTaskStatus.RUNNING.value)

PageState = Literal["ready", "attention", "working", "todo"]

# 軟標記：批次重畫主角後換上了新動作，請使用者看一下。頁面照樣算可以印（做好的頁數、可以印、
# 「印出這本書」都算），繼續做也不會因為它重排頁面流程；其他 attention（failed…）才是「請看一下」狀態。
ATTENTION_REVIEW_POSE = "review_pose"
REVIEW_POSE_DETAIL = "請看一下：換了新的主角動作"


def hard_attention(attention: str | None) -> bool:
    """擋住「可以印」的 attention：有值而且不是軟標記 review_pose。"""
    return attention is not None and attention != ATTENTION_REVIEW_POSE


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
    """B 的每頁狀態：製作中＞請看一下＞可以印＞還沒做完。線沒接起來不算請看一下（只是提示）；
    review_pose 是軟標記，PASSED 的照樣是可以印。"""
    if page.id in busy:
        return "working"
    if hard_attention(page.attention):
        return "attention"
    if page.status == PageStatus.PASSED:
        return "ready"
    return "todo"
