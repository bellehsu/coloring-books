"""C 處理一頁的「↶ 復原」「↷ 重做」與「↺ 還原成自動畫的那張」（Phase 13 §4.4）。設計見 services/history.py。

三個端點都回 PageOut（含 can_undo／can_redo）。圖、位置或筆畫有變就排頁面流程：重新合成、寫回使用者筆畫、
重算補線，跑完仍是可以印。
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from app.api.deps import current_owner, get_session, get_worker, owned_page
from app.api.pages import page_out
from app.api.schemas import PageOut, RestoreIn
from app.models import Owner
from app.services import history
from app.services.pipeline import enqueue_page_task
from app.services.worker import GenerationWorker

router = APIRouter(prefix="/api", tags=["history"])


def _run(session: Session, worker: GenerationWorker, owner: Owner, page_id: int, action) -> PageOut:
    page = owned_page(session, owner, page_id)
    session.refresh(page)
    try:
        rerun = action(session, page)
        if rerun:
            enqueue_page_task(session, page_id)
        session.commit()
    except history.HistoryError as e:
        session.rollback()
        raise HTTPException(409, e.message) from e
    except Exception:
        session.rollback()
        raise
    if rerun:
        worker.notify()
    session.refresh(page)
    return page_out(session, page)


@router.post("/pages/{page_id}/undo", response_model=PageOut)
def undo(
    page_id: int,
    session: Session = Depends(get_session),
    worker: GenerationWorker = Depends(get_worker),
    owner: Owner = Depends(current_owner),
) -> PageOut:
    """回到上一個快照；沒有可復原的 409「沒有可以復原的步驟」。"""
    return _run(session, worker, owner, page_id, history.undo)


@router.post("/pages/{page_id}/redo", response_model=PageOut)
def redo(
    page_id: int,
    session: Session = Depends(get_session),
    worker: GenerationWorker = Depends(get_worker),
    owner: Owner = Depends(current_owner),
) -> PageOut:
    """回到復原前的下一個快照；沒有可重做的（或復原之後又改過）409「沒有可以重做的步驟」。"""
    return _run(session, worker, owner, page_id, history.redo)


@router.post("/pages/{page_id}/restore", response_model=PageOut)
def restore(
    page_id: int,
    body: RestoreIn,
    session: Session = Depends(get_session),
    worker: GenerationWorker = Depends(get_worker),
    owner: Owner = Depends(current_owner),
) -> PageOut:
    """背景／角色動作換回自動畫的那張（auto_* 不能用了就用最新一張能用的 AI 圖；都沒有 409），
    線條清掉使用者筆畫。有改動才存快照；已經是自動的那張就什麼都不做。"""
    return _run(session, worker, owner, page_id, lambda s, p: history.restore(s, p, body.item))
