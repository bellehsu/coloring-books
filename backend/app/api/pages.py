"""頁面端點：建立、列出、排序、更新、刪除。

Phase 9 移除了構圖草圖（`/pages/{id}/sketch`）與舊整頁流程的建立端點（`/pages/{id}/candidates`、`/select`）。
頁面選定圖由合成流程（api/compose.py）設定；舊流程的頁面（選定圖是 PAGE）照常顯示、補線與匯出。
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, col, delete, func, select, update

from app.api.deps import current_owner, get_session, get_storage, get_worker, owned_book, owned_page
from app.api.schemas import CaptionDisplay, PageCreate, PageOrder, PageOut, PageUpdate
from app.models import (
    Book, Generation, GenStatus, LineartEdit, Owner, Page, PageHistory, PageStatus, PageTask, PageTaskStatus,
    StrokeLayer, utcnow,
)
from app.services import history
from app.services.pipeline import enqueue_page_task
from app.services.compose import page_placement
from app.services.layout import compute_layout
from app.services.page_compose import composite_matches
from app.services.page_lineart import lineart_key, on_pipeline, strokes_json_by_generation
from app.services.slots import UPLOAD_ENGINE
from app.services.usage import refund_generations
from app.services.worker import GenerationWorker
from app.storage import Storage

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["pages"])


def compose_pending(page: Page, selected: Generation | None) -> bool:
    """選好了背景與角色動作，但選定圖不是用目前背景＋角色動作＋位置（與合成版本）合成的那張。
    缺背景或角色動作（例如舊流程頁面）時為 False。比對規則與確認合成時沿用既有合成圖相同。"""
    bg_id, pose_id = page.background_generation_id, page.pose_generation_id
    if bg_id is None or (page.include_character and pose_id is None):
        return False
    if not page.include_character:
        pose_id = None  # 不放主角：合成圖只看背景
    try:
        placement = page_placement(page.placement_json)
    except (ValueError, TypeError):
        return True
    return selected is None or not composite_matches(selected, bg_id, pose_id, placement)


def page_outs(session: Session, pages: list[Page]) -> list[PageOut]:
    """PageOut 需要繪本版面、選定圖與它的修改圖層；一次查完（頁數不多）。"""
    if not pages:
        return []
    book_ids = {p.book_id for p in pages}
    gen_ids = {p.selected_generation_id for p in pages if p.selected_generation_id is not None}
    books = {b.id: b for b in session.exec(select(Book).where(col(Book.id).in_(book_ids))).all()}
    gens = (
        {g.id: g for g in session.exec(select(Generation).where(col(Generation.id).in_(gen_ids))).all()}
        if gen_ids
        else {}
    )
    edits = strokes_json_by_generation(session, gen_ids)
    with_user = set(session.exec(
        select(LineartEdit.generation_id).where(col(LineartEdit.generation_id).in_(gen_ids),
                                                col(LineartEdit.layer) == StrokeLayer.USER,
                                                col(LineartEdit.strokes_json) != "[]")
    ).all()) if gen_ids else set()
    flags = history.history_flags(session, pages)
    layouts = {bid: compute_layout(b.paper_size, b.print_mode) for bid, b in books.items()}
    out = []
    for page in pages:
        gen = gens.get(page.selected_generation_id)
        lay = layouts[page.book_id]
        pending = compose_pending(page, gen)
        can_undo, can_redo = flags[page.id]
        if page.pending_user_strokes is not None:
            has_user = history.user_strokes_json(session, page) != history.CLEARED
        else:
            has_user = page.selected_generation_id in with_user
        extra = {"can_undo": can_undo, "can_redo": can_redo, "has_user_strokes": has_user}
        if gen is None:
            out.append(PageOut.from_page(page, compose_pending=pending, **extra))
            continue
        key = lineart_key(gen, page, lay.content_px, edits.get(gen.id, "[]"))
        out.append(PageOut.from_page(
            page,
            current_lineart_key=key,
            aspect_mismatch=(gen.width, gen.height) != tuple(lay.generation_size),
            compose_pending=pending,
            **extra,
        ))
    return out


def page_out(session: Session, page: Page) -> PageOut:
    return page_outs(session, [page])[0]


@router.post("/books/{book_id}/pages", status_code=201, response_model=PageOut)
def create_page(
    book_id: int,
    body: PageCreate,
    session: Session = Depends(get_session),
    owner: Owner = Depends(current_owner),
) -> PageOut:
    owned_book(session, owner, book_id)
    top = session.exec(select(func.max(Page.position)).where(Page.book_id == book_id)).one()
    page = Page(book_id=book_id, position=0 if top is None else top + 1, scene_prompt=body.scene_prompt, action_prompt=body.action_prompt)
    session.add(page)
    session.commit()
    return page_out(session, page)


@router.get("/books/{book_id}/pages", response_model=list[PageOut])
def list_pages(
    book_id: int,
    session: Session = Depends(get_session),
    owner: Owner = Depends(current_owner),
) -> list[PageOut]:
    owned_book(session, owner, book_id)
    pages = session.exec(
        select(Page).where(Page.book_id == book_id).order_by(col(Page.position), col(Page.id))
    ).all()
    return page_outs(session, list(pages))


@router.post("/books/{book_id}/pages/order", response_model=list[PageOut])
def reorder_pages(
    book_id: int,
    body: PageOrder,
    session: Session = Depends(get_session),
    owner: Owner = Depends(current_owner),
) -> list[PageOut]:
    owned_book(session, owner, book_id)
    pages = {
        p.id: p for p in session.exec(select(Page).where(Page.book_id == book_id)).all()
    }
    if len(body.page_ids) != len(set(body.page_ids)) or set(body.page_ids) != set(pages):
        raise HTTPException(400, "page_ids 必須剛好是這本繪本的全部頁面，不可重複")
    now = utcnow()
    for pos, pid in enumerate(body.page_ids):
        pages[pid].position = pos
        pages[pid].updated_at = now
        session.add(pages[pid])
    session.commit()
    return page_outs(session, [pages[pid] for pid in body.page_ids])


@router.post("/books/{book_id}/caption-display", response_model=list[PageOut])
def set_caption_display(
    book_id: int,
    body: CaptionDisplay,
    session: Session = Depends(get_session),
    owner: Owner = Depends(current_owner),
) -> list[PageOut]:
    """C 的「把這個選擇套用到整本書」：每一頁的要印哪幾行都設成一樣。不存復原快照（不是這一頁的修改）、
    不動線稿、QA 與頁面狀態。回傳整本書的頁面（依頁序）。"""
    owned_book(session, owner, book_id)
    session.exec(
        update(Page).where(col(Page.book_id) == book_id)
        .values(caption_show_zh=body.show_zh, caption_show_en=body.show_en, updated_at=utcnow())
    )
    session.commit()
    pages = session.exec(
        select(Page).where(Page.book_id == book_id).order_by(col(Page.position), col(Page.id))
    ).all()
    return page_outs(session, list(pages))


@router.patch("/pages/{page_id}", response_model=PageOut)
def update_page(
    page_id: int,
    body: PageUpdate,
    oneclick: bool = Query(False, description="C 處理一頁：先存復原快照、排頁面流程"),
    from_setup: bool = Query(False, description="設定頁改既有頁面：場景／動作變了就標「要重畫」"),
    session: Session = Depends(get_session),
    worker: GenerationWorker = Depends(get_worker),
    owner: Owner = Depends(current_owner),
) -> PageOut:
    """oneclick（C 的改動作文字、改字、要印哪幾行）：動作文字、圖下方的字或要印哪幾行變了就先存復原快照；
    線條設定變了，流程做的頁排流程重跑（不退回草稿）。
    from_setup（設定頁的「編輯」）：場景變了而頁面有 AI 背景（上傳的不算）→ redraw_background；動作變了而
    頁面有角色動作 → redraw_pose，等「繼續做」重畫。改成空白、圖下方的字不標（改成空白也清掉那一格的旗標：
    沒有東西可以畫）；沒帶 from_setup 時不標；與 oneclick 一起帶是 400。"""
    if from_setup and oneclick:
        raise HTTPException(400, "參數衝突")
    page = owned_page(session, owner, page_id)
    fields = body.model_fields_set
    if from_setup:  # 改成空白不標、標了的清掉：沒有東西可以畫
        if body.scene_prompt is not None and not body.scene_prompt.strip():
            page.redraw_background = False
        if body.action_prompt is not None and not body.action_prompt.strip():
            page.redraw_pose = False
        if (body.scene_prompt and body.scene_prompt != page.scene_prompt
                and page.background_generation_id is not None):
            bg = session.get(Generation, page.background_generation_id)
            if bg is not None and bg.engine != UPLOAD_ENGINE:
                page.redraw_background = True
        if (body.action_prompt and body.action_prompt != page.action_prompt
                and page.pose_generation_id is not None):
            page.redraw_pose = True
        if (body.include_character is not None and body.include_character != page.include_character
                and page.background_generation_id is not None):  # 主角有沒有放變了：AI 背景要重畫（上傳的不動）
            bg = session.get(Generation, page.background_generation_id)
            if bg is not None and bg.engine != UPLOAD_ENGINE:
                page.redraw_background = True
    if oneclick and (
        (body.action_prompt is not None and body.action_prompt != page.action_prompt)
        or ("caption_zh" in fields and body.caption_zh != page.caption_zh)
        or ("caption_en" in fields and body.caption_en != page.caption_en)
        or (body.caption_show_zh is not None and body.caption_show_zh != page.caption_show_zh)
        or (body.caption_show_en is not None and body.caption_show_en != page.caption_show_en)
        or (body.include_character is not None and body.include_character != page.include_character)
    ):
        history.push_snapshot(session, page)
        session.refresh(page)
    if body.scene_prompt is not None:
        if body.scene_prompt != page.scene_prompt:  # 改了場景文字：自動清理關掉（同一個請求明確帶的值優先，下面覆寫）
            page.auto_cleanup = False
            page.cleanup_prompt = None
        page.scene_prompt = body.scene_prompt
    if body.auto_cleanup is not None:
        page.auto_cleanup = body.auto_cleanup
    if "cleanup_prompt" in fields:
        page.cleanup_prompt = body.cleanup_prompt
    if body.action_prompt is not None:
        page.action_prompt = body.action_prompt
    if body.include_character is not None:
        page.include_character = body.include_character
    # 頁面文字：用 model_fields_set 區分「沒送」與「送 null／空字串＝清除」；不動生成紀錄、線稿與 QA 狀態
    if "caption_zh" in body.model_fields_set:
        page.caption_zh = body.caption_zh
    if "caption_en" in body.model_fields_set:
        page.caption_en = body.caption_en
    # 要印哪幾行：只影響列印頁（print_page_image），線稿與 QA 不變
    if body.caption_show_zh is not None:
        page.caption_show_zh = body.caption_show_zh
    if body.caption_show_en is not None:
        page.caption_show_en = body.caption_show_en
    lines_changed = False
    if body.line_detail is not None and body.line_detail != page.line_detail:
        page.line_detail = body.line_detail
        lines_changed = True
    if body.line_width_px is not None and body.line_width_px != page.line_width_px:
        page.line_width_px = body.line_width_px
        lines_changed = True
    rerun = False
    if lines_changed and oneclick and _on_pipeline(session, page):
        enqueue_page_task(session, page_id)  # 流程重算補線，跑完仍可以印
        rerun = True
    elif lines_changed and page.status == PageStatus.PASSED:
        # 線稿變了要重新檢查；彩圖沒變，所以角色檢查保留
        page.status = PageStatus.DRAFT
        page.passed_at = None
    page.updated_at = utcnow()
    session.add(page)
    session.commit()
    if rerun:
        worker.notify()
    return page_out(session, page)


def _on_pipeline(session: Session, page: Page) -> bool:
    gen = session.get(Generation, page.selected_generation_id) if page.selected_generation_id else None
    return gen is not None and on_pipeline(page, gen)


@router.delete("/pages/{page_id}", status_code=204)
def delete_page(
    page_id: int,
    session: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
    owner: Owner = Depends(current_owner),
) -> None:
    page = owned_page(session, owner, page_id)
    book_id = page.book_id
    # 先取消排隊中的（條件式 UPDATE 取得寫入鎖，worker 之後無法再認領），再檢查有沒有執行中的
    cancelled = session.exec(
        update(Generation)
        .where(col(Generation.page_id) == page_id, col(Generation.status) == GenStatus.QUEUED)
        .values(status=GenStatus.CANCELLED, error="頁面已刪除", finished_at=utcnow())
        .returning(Generation.id)
    ).scalars().all()
    refund_generations(session, cancelled)  # 排隊中被取消的退回額度（頁面若刪不掉會一起 rollback）
    running = session.exec(
        select(
            select(Generation.id)
            .where(col(Generation.page_id) == page_id, col(Generation.status) == GenStatus.RUNNING)
            .exists()
        )
    ).one()
    if running:
        session.rollback()
        raise HTTPException(409, "這一頁還有圖片正在生成，請稍後再刪")
    if session.exec(select(select(PageTask.id).where(
        col(PageTask.page_id) == page_id, col(PageTask.status) == PageTaskStatus.RUNNING.value
    ).exists())).one():
        session.rollback()  # 頁面流程正在寫這頁的合成圖與線稿
        raise HTTPException(409, "這一頁正在自動處理，請稍後再刪")
    gens = session.exec(select(Generation).where(col(Generation.page_id) == page_id)).all()
    image_paths = [p for g in gens for p in (g.image_path, g.mask_path) if p]
    session.exec(delete(LineartEdit).where(col(LineartEdit.page_id) == page_id))
    session.exec(delete(PageTask).where(col(PageTask.page_id) == page_id))
    session.exec(delete(PageHistory).where(col(PageHistory.page_id) == page_id))
    session.exec(delete(Generation).where(col(Generation.page_id) == page_id))
    session.exec(delete(Page).where(col(Page.id) == page_id))
    rest = session.exec(
        select(Page).where(col(Page.book_id) == book_id).order_by(col(Page.position), col(Page.id))
    ).all()
    for i, p in enumerate(rest):
        if p.position != i:
            p.position = i
            session.add(p)
    session.commit()
    # 物件在交易提交後才刪（盡力而為）：每個刪除各自處理錯誤，一個失敗不影響其他的；
    # 資料庫已提交，失敗只記 log、不回 500，留下的孤兒物件由 storage_gc 清
    def attempt(what: str, fn, *args) -> None:
        try:
            fn(*args)
        except Exception:  # noqa: BLE001
            log.warning("刪除頁面 %s 的物件失敗：%s", page_id, what, exc_info=True)

    for key in image_paths:
        attempt(key, storage.delete, key)
    # 結尾的 / 不可省：books/1/pages/1 會連 books/1/pages/10 一起刪
    page_prefix = f"books/{book_id}/pages/{page_id}/"
    attempt(page_prefix, storage.delete_prefix, page_prefix)
