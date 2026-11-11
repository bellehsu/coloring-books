"""頁面端點：建立、列出、排序、更新、刪除。

Phase 9 移除了構圖草圖（`/pages/{id}/sketch`）與舊整頁流程的建立端點（`/pages/{id}/candidates`、`/select`）。
頁面選定圖由合成流程（api/compose.py）設定；舊流程的頁面（選定圖是 PAGE）照常顯示、補線與匯出。
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import case, literal, null
from sqlmodel import Session, col, delete, func, select, update

from app.api.deps import current_owner, get_session, get_storage, owned_book, owned_page
from app.api.schemas import PageCreate, PageOrder, PageOut, PageUpdate
from app.models import Book, Generation, GenStatus, LineartEdit, Owner, Page, PageStatus, utcnow
from app.services.compose import composite_matches, page_placement
from app.services.layout import compute_layout
from app.services.page_lineart import lineart_key
from app.services.usage import refund_generations
from app.storage import Storage

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["pages"])



def reset_review_when(cond) -> dict:
    """條件式 UPDATE 用的 SET 片段：cond 成立（以寫入當下的舊值判斷）時退回草稿，
    角色檢查、通過時間與強制通過原因作廢；不成立時保持原值。"""
    return {
        "status": case(
            (cond, literal(PageStatus.DRAFT, Page.__table__.c.status.type)),
            else_=col(Page.status),
        ),
        "passed_at": case((cond, null()), else_=col(Page.passed_at)),
        "character_check": case((cond, null()), else_=col(Page.character_check)),
        "forced_pass_reason": case((cond, null()), else_=col(Page.forced_pass_reason)),
    }


def compose_pending(page: Page, selected: Generation | None) -> bool:
    """選好了背景與角色動作，但選定圖不是用目前背景＋角色動作＋位置（與合成版本）合成的那張。
    缺背景或角色動作（例如舊流程頁面）時為 False。比對規則與確認合成時沿用既有合成圖相同。"""
    bg_id, pose_id = page.background_generation_id, page.pose_generation_id
    if bg_id is None or pose_id is None:
        return False
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
    edits = (
        {
            e.generation_id: e.strokes_json
            for e in session.exec(
                select(LineartEdit).where(col(LineartEdit.generation_id).in_(gen_ids))
            ).all()
        }
        if gen_ids
        else {}
    )
    layouts = {bid: compute_layout(b.paper_size, b.print_mode) for bid, b in books.items()}
    out = []
    for page in pages:
        gen = gens.get(page.selected_generation_id)
        lay = layouts[page.book_id]
        pending = compose_pending(page, gen)
        if gen is None:
            out.append(PageOut.from_page(page, compose_pending=pending))
            continue
        key = lineart_key(gen, page, lay.content_px, edits.get(gen.id, "[]"))
        out.append(PageOut.from_page(
            page,
            current_lineart_key=key,
            aspect_mismatch=(gen.width, gen.height) != tuple(lay.generation_size),
            compose_pending=pending,
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


@router.patch("/pages/{page_id}", response_model=PageOut)
def update_page(
    page_id: int,
    body: PageUpdate,
    session: Session = Depends(get_session),
    owner: Owner = Depends(current_owner),
) -> PageOut:
    page = owned_page(session, owner, page_id)
    if body.scene_prompt is not None:
        page.scene_prompt = body.scene_prompt
    if body.action_prompt is not None:
        page.action_prompt = body.action_prompt
    # body.controlnet_strength：構圖草圖已停用，舊前端送的值忽略
    lines_changed = False
    if body.line_detail is not None and body.line_detail != page.line_detail:
        page.line_detail = body.line_detail
        lines_changed = True
    if body.line_width_px is not None and body.line_width_px != page.line_width_px:
        page.line_width_px = body.line_width_px
        lines_changed = True
    if lines_changed and page.status == PageStatus.PASSED:
        # 線稿變了要重新檢查；彩圖沒變，所以角色檢查保留
        page.status = PageStatus.DRAFT
        page.passed_at = None
    page.updated_at = utcnow()
    session.add(page)
    session.commit()
    return page_out(session, page)


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
    gens = session.exec(select(Generation).where(col(Generation.page_id) == page_id)).all()
    gen_ids = [g.id for g in gens]
    image_paths = [p for g in gens for p in (g.image_path, g.mask_path) if p]
    session.exec(delete(LineartEdit).where(col(LineartEdit.page_id) == page_id))
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
    for gid in gen_ids:
        prefix = f"cache/previews/{gid}_"
        try:
            keys = list(storage.list(prefix))
        except Exception:  # noqa: BLE001
            log.warning("刪除頁面 %s 的物件失敗：列出 %s", page_id, prefix, exc_info=True)
            continue
        for key in keys:
            attempt(key, storage.delete, key)
