"""C 處理一頁的「↶ 復原」「↷ 重做」與「↺ 還原成自動畫的那張」（Phase 13 §4.4）。設計見 services/history.py。

三個端點都回 PageOut（含 can_undo／can_redo）。圖、位置或筆畫有變就排頁面流程：重新合成、寫回使用者筆畫、
重算補線，跑完仍是可以印。

「這頁可以印了 ✓」（accept）：清掉「請看一下」、標成可以印（已經可以印的保留通過時間）。不是內容的改動，
不存快照、不排流程。還在等重新合成（筆畫等著寫回）時不行：先等它做完或「再試一次」。

「再試一次」（retry）：只處理這一頁。這頁最新一張背景／角色動作是失敗的（之後沒有成功的同一種）就重排那一種
（同 C 的「換一張」：take，好了就換上去；算 1 次當日額度，用完 429）；沒有就重跑這頁的頁面流程（不計次數）。
失敗的背景清理（「清掉框外雜物」）不算：原本的背景好好的，不用重畫。
與「繼續做」共用 BATCH_LOCK，同時按兩次只排一次、只算一次。
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import case, func, literal
from sqlmodel import Session, col, select, update

from app.api.deps import current_owner, get_session, get_worker, owned_book, owned_page
from app.api.pages import page_out
from app.api.schemas import PageOut, RestoreIn, RetryOut
from app.models import Generation, GenKind, GenStatus, Owner, Page, PageStatus, utcnow
from app.services import history
from app.services.gen_request import is_cleanup
from app.services.jobs import BATCH_LOCK, background_job, enqueue_jobs, has_character, pose_job, take
from app.services.pipeline import enqueue_page_task
from app.services.slots import not_candidate
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


NO_SELECTION = "這一頁還沒有選定圖片"
CHANGED = "頁面剛被修改，請再按一次"
STILL_RECOMPOSING = "這一頁還在重新合成，請等它做完或按「再試一次」"
MISSING_IMAGES = "這一頁還缺背景或角色動作，請先畫一張"
NO_CHARACTER = "請先設定角色"


@router.post("/pages/{page_id}/accept", response_model=PageOut)
def accept(
    page_id: int,
    session: Session = Depends(get_session),
    owner: Owner = Depends(current_owner),
) -> PageOut:
    """C 的「這頁可以印了 ✓」：有選定圖（合成圖；舊流程的整頁圖也算）就清掉 attention、標成可以印。
    沒有選定圖 409。不需要角色檢查（新流程的頁面自動可以印，spec §4.1）。"""
    page = owned_page(session, owner, page_id)
    session.refresh(page)
    gid = page.selected_generation_id
    if gid is None:
        raise HTTPException(409, NO_SELECTION)
    if page.pending_user_strokes is not None:  # 選定圖還是舊的合成圖：標成可以印會印錯、C 也一直等著
        raise HTTPException(409, STILL_RECOMPOSING)
    now = utcnow()
    already = col(Page.status) == PageStatus.PASSED
    res = session.exec(
        update(Page)
        .where(col(Page.id) == page_id, col(Page.selected_generation_id) == gid,
               col(Page.pending_user_strokes).is_(None))
        .values(status=PageStatus.PASSED,
                passed_at=func.coalesce(case((already, col(Page.passed_at))),
                                        literal(now, Page.__table__.c.passed_at.type)),
                attention=None, attention_detail=None, updated_at=now)
    )
    session.commit()
    if res.rowcount == 0:
        raise HTTPException(409, CHANGED)
    session.refresh(page)
    return page_out(session, page)


def _newest_query(page_id: int, kind: GenKind):
    """這頁這種素材不是候選的生成（不論狀態），由新到舊。"""
    return (select(Generation).where(Generation.page_id == page_id, Generation.kind == kind, not_candidate())
            .order_by(col(Generation.id).desc()))


def _newest(session: Session, page_id: int, kind: GenKind) -> Generation | None:
    """這頁這種素材最新一張不是候選的生成（不論狀態）。"""
    return session.exec(_newest_query(page_id, kind)).first()


def _failed_kinds(session: Session, page_id: int) -> list[GenKind]:
    """這頁最新一張（依 id）是失敗的背景／角色動作種類（之後有成功的、或還在排隊／執行中就不算）。
    重畫主角的候選不算：它失敗不用重畫，它成功也不代表失敗的 take 補好了。
    失敗的背景清理略過（原本的背景好好的，不用重畫）；略過後再往前看，清理之前就失敗的背景照樣算。"""
    out = []
    for kind in (GenKind.BACKGROUND, GenKind.POSE):
        newest = next((g for g in session.exec(_newest_query(page_id, kind)).all()
                       if not (g.status == GenStatus.FAILED and is_cleanup(g))), None)
        if newest is not None and newest.status == GenStatus.FAILED:
            out.append(kind)
    return out


@router.post("/pages/{page_id}/retry", response_model=RetryOut)
def retry(
    page_id: int,
    session: Session = Depends(get_session),
    worker: GenerationWorker = Depends(get_worker),
    owner: Owner = Depends(current_owner),
) -> RetryOut:
    """C 的「再試一次」，只處理這一頁（見模組說明）。重排失敗的圖：額度不夠 429、什麼都不排；
    沒有失敗的圖、也沒有背景＋角色動作 409。
    與「繼續做」共用 BATCH_LOCK：兩個分頁同時按，第二個在鎖裡重看時那一種已經在排隊、不再算失敗，
    不會重複排、重複算額度。"""
    with BATCH_LOCK:  # 鎖裡才讀頁面與失敗的圖（同 produce）
        page = owned_page(session, owner, page_id)
        session.refresh(page)
        book = owned_book(session, owner, page.book_id)
        kinds = _failed_kinds(session, page_id)
        if kinds:
            if GenKind.POSE in kinds and not has_character(session, book):
                raise HTTPException(409, NO_CHARACTER)
            jobs = []
            for kind in kinds:
                if kind == GenKind.BACKGROUND:
                    jobs.append(take(background_job(session, book, page), True))
                else:
                    failed = _newest(session, page_id, kind)
                    action = (failed.user_text if failed else "") or page.action_prompt
                    jobs.append(take(pose_job(session, book, page, action), True))
            enqueue_jobs(session, worker, owner, jobs)  # 預留額度＋排隊＋commit；不夠 429
            session.refresh(page)
            return RetryOut(**page_out(session, page).model_dump(),
                            requeued=[k.value for k in kinds], pipeline=False)
        if page.background_generation_id is None or page.pose_generation_id is None:
            raise HTTPException(409, MISSING_IMAGES)
        try:
            enqueue_page_task(session, page_id)
            session.commit()
        except Exception:
            session.rollback()
            raise
        worker.notify()
        session.refresh(page)
        return RetryOut(**page_out(session, page).model_dump(), requeued=[], pipeline=True)
