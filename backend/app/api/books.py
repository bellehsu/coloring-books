"""繪本與角色端點。"""

import json
import logging
import random

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlmodel import Session, col, select, update

from app.api.deps import get_session, get_worker
from app.api.schemas import (
    BookCreate,
    BookOut,
    BookStats,
    BookUpdate,
    CharacterCandidatesCreate,
    CharacterSelect,
    GenerationOut,
)
from app.models import Book, Generation, GenKind, GenStatus, Page, PageStatus, utcnow
from app.services.layout import PaperSize, ratio_group
from app.services.page_lineart import ensure_page_lineart
from app.services.prompts import QWEN_CHARACTER_SIZE, pick_character_variation, qwen_character_prompt
from app.services.worker import GenerationWorker, encode_request

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/books", tags=["books"])

CHARACTER_SIZE = QWEN_CHARACTER_SIZE
MAX_SEED = 2**32 - 1


def _book_or_404(session: Session, book_id: int) -> Book:
    book = session.get(Book, book_id)
    if book is None:
        raise HTTPException(404, "找不到這本繪本")
    return book


def book_outs(session: Session, books: list[Book]) -> list[BookOut]:
    """附上統計與封面；所有繪本的頁面一次查完，封面頁再交給 page_outs 算 lineart_url。"""
    from app.api.pages import page_outs  # 避免與 pages.py 循環匯入

    ids = [b.id for b in books]
    by_book: dict[int, list[Page]] = {i: [] for i in ids}
    if ids:
        for p in session.exec(
            select(Page).where(col(Page.book_id).in_(ids)).order_by(col(Page.position))
        ).all():
            by_book[p.book_id].append(p)
    covers = {}
    for bid, pages in by_book.items():
        sel = [p for p in pages if p.selected_generation_id is not None]
        if sel:
            covers[bid] = sel[0]  # 已依 position 排序
    cover_urls = {
        p.book_id: o.lineart_url for p, o in zip(covers.values(), page_outs(session, list(covers.values())))
    }
    out = []
    for b in books:
        pages = by_book[b.id]
        stats = BookStats(
            pages=len(pages),
            passed=sum(p.status == PageStatus.PASSED for p in pages),
            needs_fix=sum(
                p.selected_generation_id is not None
                and bool(p.qa_json)
                and bool(json.loads(p.qa_json).get("errors"))
                for p in pages
            ),
            no_selection=sum(p.selected_generation_id is None for p in pages),
        )
        out.append(BookOut.from_book(b, stats, cover_urls.get(b.id)))
    return out


def book_out(session: Session, book: Book) -> BookOut:
    return book_outs(session, [book])[0]


@router.post("", status_code=201, response_model=BookOut)
def create_book(body: BookCreate, session: Session = Depends(get_session)) -> BookOut:
    book = Book(title=body.title, paper_size=body.paper_size, print_mode=body.print_mode)
    session.add(book)
    session.commit()
    return book_out(session, book)


@router.get("", response_model=list[BookOut])
def list_books(session: Session = Depends(get_session)) -> list[BookOut]:
    books = session.exec(select(Book).order_by(col(Book.id).desc())).all()
    return book_outs(session, list(books))


@router.get("/{book_id}", response_model=BookOut)
def get_book(book_id: int, session: Session = Depends(get_session)) -> BookOut:
    return book_out(session, _book_or_404(session, book_id))


def _recompute_book_lineart(sessions, storage, book_id: int) -> None:
    """改尺寸／列印方式後，背景重算每個有選圖頁面的線稿（單頁失敗只記 log）。"""
    with sessions() as session:
        ids = session.exec(
            select(Page.id)
            .where(col(Page.book_id) == book_id, col(Page.selected_generation_id).is_not(None))
            .order_by(col(Page.id))
        ).all()
        for page_id in ids:
            try:
                ensure_page_lineart(session, storage, page_id)
            except Exception:
                session.rollback()
                log.warning("book %s page %s 線稿重算失敗", book_id, page_id, exc_info=True)


@router.patch("/{book_id}", response_model=BookOut)
def update_book(
    book_id: int,
    body: BookUpdate,
    request: Request,
    background: BackgroundTasks,
    session: Session = Depends(get_session),
) -> BookOut:
    book = _book_or_404(session, book_id)
    if body.paper_size is not None and ratio_group(body.paper_size) != ratio_group(book.paper_size):
        same = "、".join(
            s.name if s.name[0] != "S" else s.value
            for s in PaperSize
            if ratio_group(s) == ratio_group(book.paper_size)
        )
        raise HTTPException(422, f"只能改成同比例的紙張尺寸（{same}）")
    changed = False
    if body.title is not None:
        book.title = body.title
    if body.paper_size is not None and body.paper_size != book.paper_size:
        book.paper_size = body.paper_size
        changed = True
    if body.print_mode is not None and body.print_mode != book.print_mode:
        book.print_mode = body.print_mode
        changed = True
    if body.title is not None or changed:
        book.updated_at = utcnow()
        session.add(book)
        session.commit()
    if changed:
        background.add_task(
            _recompute_book_lineart, request.app.state.sessions, request.app.state.storage, book_id
        )
    return book_out(session, book)


def _last_character_variation(session: Session, book_id: int) -> str | None:
    """這本書最近一筆有變化語的角色候選（依 id，不論狀態、是否丟棄）用的變化語；沒有回 None。
    沒有變化語的舊列（SDXL 時代）略過。"""
    for rj in session.exec(
        select(Generation.request_json)
        .where(Generation.book_id == book_id, Generation.kind == GenKind.CHARACTER)
        .order_by(col(Generation.id).desc())
    ):
        try:
            d = json.loads(rj)
        except (TypeError, ValueError):
            continue
        if isinstance(d, dict) and isinstance(d.get("variation"), str):
            return d["variation"]
    return None


@router.post("/{book_id}/character/candidates", status_code=202, response_model=list[GenerationOut])
def create_character_candidates(
    book_id: int,
    body: CharacterCandidatesCreate,
    session: Session = Depends(get_session),
    worker: GenerationWorker = Depends(get_worker),
) -> list[GenerationOut]:
    """產生一張角色候選圖（Qwen 純文字生圖）；描述不翻譯、原樣放進樣板。回傳只含這一張的清單。"""
    book = _book_or_404(session, book_id)
    if book.character_generation_id is not None:
        raise HTTPException(409, "這本繪本的角色已經設定，不能再產生候選圖")
    # 一次一張：body.count 是舊前端送的，忽略。再按一次＝再產生一張，變化語不重複這本書上一張
    seed = body.seed if body.seed is not None else random.randint(0, MAX_SEED)
    variation = pick_character_variation(
        random.Random(), exclude=_last_character_variation(session, book_id)
    )
    prompt = qwen_character_prompt(body.description, variation)
    width, height = CHARACTER_SIZE
    gen = Generation(
        book_id=book_id,
        kind=GenKind.CHARACTER,
        user_text=body.description,
        prompt=prompt,
        negative_prompt="",  # Qwen cfg 1：負向無效，排除條件寫在樣板正向
        seed=seed,
        width=width,
        height=height,
        request_json=encode_request(
            kind=GenKind.CHARACTER,
            prompt=prompt,
            negative_prompt="",
            seed=seed,
            width=width,
            height=height,
            variation=variation,
        ),
    )
    # 先以條件式 UPDATE 取得寫入鎖並確認角色仍未設定，與 set_character 互斥
    res = session.exec(
        update(Book)
        .where(col(Book.id) == book_id, col(Book.character_generation_id).is_(None))
        .values(updated_at=utcnow())
    )
    if res.rowcount == 0:
        session.rollback()
        raise HTTPException(409, "這本繪本的角色已經設定，不能再產生候選圖")
    session.add(gen)
    session.commit()
    worker.notify()
    # 回傳仍是清單（現行前端以清單處理候選）
    return [
        GenerationOut.from_generation(
            gen, queue_position=worker.queue_position(gen.id), progress=worker.progress.get(gen.id)
        )
    ]


@router.post("/{book_id}/character", response_model=BookOut)
def set_character(
    book_id: int, body: CharacterSelect, session: Session = Depends(get_session)
) -> BookOut:
    book = _book_or_404(session, book_id)
    if book.character_generation_id is not None:
        raise HTTPException(409, "這本繪本的角色已經設定")
    gen = session.get(Generation, body.generation_id)
    if gen is None:
        raise HTTPException(404, "找不到這筆生成紀錄")
    if gen.book_id != book_id:
        raise HTTPException(409, "這筆生成紀錄不屬於這本繪本")
    if gen.kind != GenKind.CHARACTER:
        raise HTTPException(409, "只能選擇角色候選圖")
    if gen.status != GenStatus.SUCCEEDED:
        raise HTTPException(409, "這張候選圖尚未成功生成")
    if gen.discarded:
        raise HTTPException(409, "這張候選圖已被捨棄")
    # 同一條 UPDATE 再確認候選圖仍可用，避免與捨棄競爭而指向已捨棄的 generation
    still_valid = (
        select(Generation.id)
        .where(
            col(Generation.id) == gen.id,
            col(Generation.book_id) == book_id,
            col(Generation.kind) == GenKind.CHARACTER,
            col(Generation.status) == GenStatus.SUCCEEDED,
            col(Generation.discarded).is_(False),
        )
        .exists()
    )
    res = session.exec(
        update(Book)
        .where(
            col(Book.id) == book_id,
            col(Book.character_generation_id).is_(None),
            still_valid,
        )
        .values(
            character_generation_id=gen.id,
            character_description=gen.user_text,
            updated_at=utcnow(),
        )
    )
    if res.rowcount == 0:
        session.rollback()
        raise HTTPException(409, "這本繪本的角色已經設定，或這張候選圖已不可用")
    # 角色已定案：同本書其餘排隊中的角色候選不再需要，同一交易取消（執行中的不動）
    session.exec(
        update(Generation)
        .where(
            col(Generation.book_id) == book_id,
            col(Generation.kind) == GenKind.CHARACTER,
            col(Generation.status) == GenStatus.QUEUED,
        )
        .values(status=GenStatus.CANCELLED, finished_at=utcnow())
    )
    session.commit()
    session.refresh(book)
    return book_out(session, book)
