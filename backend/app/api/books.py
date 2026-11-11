"""繪本與角色端點。

角色可以換（Phase 13）：已設定角色時，產生候選圖、選角色、上傳主角都要帶 confirm_change=true，否則 409。
換了角色，既有的角色動作留著，之後的角色動作用新的角色。
"""

import io
import json
import logging
import random
import time

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request, UploadFile
from sqlalchemy import true
from sqlmodel import Session, col, select, update

from app.api.deps import current_owner, get_session, get_storage, get_worker, owned_book, owned_generation
from app.api.schemas import (
    BookCreate,
    BookOut,
    BookStats,
    BookUpdate,
    CharacterCandidatesCreate,
    CharacterSelect,
    GenerationOut,
    ShareIn,
)
from app.models import Book, Generation, GenKind, GenStatus, Owner, Page, PageStatus, utcnow
from app.services.layout import PaperSize, ratio_group
from app.services.page_compose import store_outside_tx
from app.services.page_lineart import ensure_page_lineart, on_pipeline
from app.services.pipeline import UPLOAD_ENGINE, enqueue_page_task
from app.services import usage
from app.services.upload_bg import letterbox
from app.storage import Storage
from app.services.prompts import QWEN_CHARACTER_SIZE, pick_character_variation, qwen_character_prompt
from app.services.worker import GenerationWorker, encode_request

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/books", tags=["books"])

CHARACTER_SIZE = QWEN_CHARACTER_SIZE
MAX_SEED = 2**32 - 1



def book_outs(session: Session, books: list[Book]) -> list[BookOut]:
    """附上統計與封面；所有繪本的頁面一次查完，封面頁再交給 page_outs 算 lineart_url。"""
    from app.api.pages import page_outs  # 避免與 pages.py 循環匯入
    from app.api.produce import busy_page_ids, page_state  # 同上（produce 匯入 pages）

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
    busy = busy_page_ids(session, ids)
    out = []
    for b in books:
        pages = by_book[b.id]
        states = [page_state(p, busy) for p in pages]
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
            ready=states.count("ready"),
            attention=states.count("attention"),
            working=states.count("working"),
            todo=states.count("todo"),
        )
        out.append(BookOut.from_book(b, stats, cover_urls.get(b.id)))
    return out


def book_out(session: Session, book: Book) -> BookOut:
    return book_outs(session, [book])[0]


@router.post("", status_code=201, response_model=BookOut)
def create_book(
    body: BookCreate,
    session: Session = Depends(get_session),
    owner: Owner = Depends(current_owner),
) -> BookOut:
    book = Book(title=body.title, paper_size=body.paper_size, print_mode=body.print_mode, owner_id=owner.id)
    session.add(book)
    session.commit()
    return book_out(session, book)


@router.get("", response_model=list[BookOut])
def list_books(
    session: Session = Depends(get_session), owner: Owner = Depends(current_owner)
) -> list[BookOut]:
    """自己的書；admin 看全部。"""
    stmt = select(Book)
    if not owner.is_admin:
        stmt = stmt.where(col(Book.owner_id) == owner.id)
    books = session.exec(stmt.order_by(col(Book.id).desc())).all()
    return book_outs(session, list(books))


@router.get("/{book_id}", response_model=BookOut)
def get_book(
    book_id: int,
    session: Session = Depends(get_session),
    owner: Owner = Depends(current_owner),
) -> BookOut:
    return book_out(session, owned_book(session, owner, book_id))


def _recompute_book_lineart(sessions, storage, book_id: int, skip: frozenset[int] = frozenset()) -> None:
    """改尺寸／列印方式後，背景重算每個有選圖頁面的線稿（單頁失敗只記 log）。skip 的頁面已排頁面流程。"""
    with sessions() as session:
        ids = session.exec(
            select(Page.id)
            .where(col(Page.book_id) == book_id, col(Page.selected_generation_id).is_not(None))
            .order_by(col(Page.id))
        ).all()
        for page_id in ids:
            if page_id in skip:
                continue
            try:
                ensure_page_lineart(session, storage, page_id)
            except Exception:
                session.rollback()
                log.warning("book %s page %s 線稿重算失敗", book_id, page_id, exc_info=True)


def _pipeline_pages(session: Session, book_id: int) -> list[int]:
    """可以印、而且是頁面流程做的頁（背景＋角色動作的合成圖）：紙張改了要重跑流程重新補線。"""
    rows = session.exec(
        select(Page, Generation)
        .where(col(Page.book_id) == book_id, col(Page.status) == PageStatus.PASSED,
               col(Generation.id) == col(Page.selected_generation_id))
    ).all()
    return [p.id for p, g in rows if on_pipeline(p, g)]


@router.patch("/{book_id}", response_model=BookOut)
def update_book(
    book_id: int,
    body: BookUpdate,
    request: Request,
    background: BackgroundTasks,
    session: Session = Depends(get_session),
    worker: GenerationWorker = Depends(get_worker),
    owner: Owner = Depends(current_owner),
) -> BookOut:
    """改尺寸／列印方式：頁面流程做的可以印的頁排流程重跑（跑完仍可以印，不悄悄退回「還沒做完」），
    其他有選圖的頁在背景重算線稿（舊編輯器的規則：可以印的頁有缺口就退回草稿）。"""
    book = owned_book(session, owner, book_id)
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
    rerun: list[int] = []
    if body.title is not None or changed:
        book.updated_at = utcnow()
        session.add(book)
        if changed:
            rerun = _pipeline_pages(session, book_id)
            for page_id in rerun:
                enqueue_page_task(session, page_id)
        session.commit()
    if changed:
        if rerun:
            worker.notify()
        background.add_task(
            _recompute_book_lineart, request.app.state.sessions, request.app.state.storage, book_id,
            frozenset(rerun),
        )
    return book_out(session, book)


NEED_PASSED_PAGE = "至少完成一頁才能分享"


@router.put("/{book_id}/share", response_model=BookOut)
def set_share(
    book_id: int,
    body: ShareIn,
    session: Session = Depends(get_session),
    owner: Owner = Depends(current_owner),
) -> BookOut:
    """分享開關（擁有者或 admin）：開啟需要至少一個完成的頁面；關閉下一個請求就生效。"""
    book = owned_book(session, owner, book_id)
    if body.shared:
        passed = session.exec(
            select(Page.id).where(col(Page.book_id) == book_id, col(Page.status) == PageStatus.PASSED).limit(1)
        ).first()
        if passed is None:
            raise HTTPException(409, NEED_PASSED_PAGE)
        if book.shared_at is None:
            book.shared_at = utcnow()
    else:
        book.shared_at = None
    session.add(book)
    session.commit()
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
    owner: Owner = Depends(current_owner),
) -> list[GenerationOut]:
    """產生一張角色候選圖（Qwen 純文字生圖）；描述不翻譯、原樣放進樣板。回傳只含這一張的清單。"""
    book = owned_book(session, owner, book_id)
    if book.character_generation_id is not None and not body.confirm_change:
        raise HTTPException(409, "這本繪本的角色已經設定，不能再產生候選圖")
    # 一次一張：body.count 是舊前端送的，忽略。再按一次＝再產生一張，變化語不重複這本書上一張
    seed = body.seed if body.seed is not None else random.randint(0, MAX_SEED)
    variation = pick_character_variation(
        random.Random(), exclude=_last_character_variation(session, book_id)
    )
    prompt = qwen_character_prompt(body.description, variation)
    width, height = CHARACTER_SIZE
    # 先以條件式 UPDATE 取得寫入鎖並確認角色仍未設定（確認要換角色時不必），與 set_character 互斥
    unset = col(Book.character_generation_id).is_(None)
    res = session.exec(
        update(Book)
        .where(col(Book.id) == book_id, true() if body.confirm_change else unset)
        .values(updated_at=utcnow())
    )
    if res.rowcount == 0:
        session.rollback()
        raise HTTPException(409, "這本繪本的角色已經設定，不能再產生候選圖")
    try:
        day = usage.reserve(session, owner, 1, usage.clock())  # 同一個交易：429 時什麼都不寫
    except HTTPException:
        session.rollback()
        raise
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
            extra=usage.usage_extra(owner, day),
        ),
    )
    session.add(gen)
    session.commit()
    worker.notify()
    # 回傳仍是清單（現行前端以清單處理候選）
    return [
        GenerationOut.from_generation(
            gen, queue_position=worker.queue_position(gen.id), progress=worker.progress.get(gen.id)
        )
    ]


CHARACTER_SET = "這本繪本的角色已經設定"


def _select_character(
    session: Session, book_id: int, gen_id: int, description: str | None, confirm_change: bool
) -> None:
    """設定角色（同一個交易取消其餘排隊中的候選並退回額度），commit。已設定角色而沒有 confirm_change 時 409。
    條件式 UPDATE 再確認候選圖仍可用（與捨棄競爭時不會指向已捨棄的 generation）。"""
    still_valid = (
        select(Generation.id)
        .where(
            col(Generation.id) == gen_id,
            col(Generation.book_id) == book_id,
            col(Generation.kind) == GenKind.CHARACTER,
            col(Generation.status) == GenStatus.SUCCEEDED,
            col(Generation.discarded).is_(False),
            col(Generation.image_path).is_not(None),  # storage_gc 清掉圖的不能當角色
        )
        .exists()
    )
    res = session.exec(
        update(Book)
        .where(
            col(Book.id) == book_id,
            true() if confirm_change else col(Book.character_generation_id).is_(None),
            still_valid,
        )
        .values(
            character_generation_id=gen_id,
            character_description=description,
            updated_at=utcnow(),
        )
    )
    if res.rowcount == 0:
        session.rollback()
        raise HTTPException(409, "這本繪本的角色已經設定，或這張候選圖已不可用")
    # 角色已定案：同本書其餘排隊中的角色候選不再需要，同一交易取消（執行中的不動）
    cancelled = session.exec(
        update(Generation)
        .where(
            col(Generation.book_id) == book_id,
            col(Generation.kind) == GenKind.CHARACTER,
            col(Generation.status) == GenStatus.QUEUED,
        )
        .values(status=GenStatus.CANCELLED, finished_at=utcnow())
        .returning(Generation.id)
    ).scalars().all()
    usage.refund_generations(session, cancelled)  # 取消的候選退回額度
    session.commit()


@router.post("/{book_id}/character", response_model=BookOut)
def set_character(
    book_id: int,
    body: CharacterSelect,
    session: Session = Depends(get_session),
    owner: Owner = Depends(current_owner),
) -> BookOut:
    """選角色。已設定角色時要 confirm_change=true（換角色：既有的角色動作留著），否則 409。"""
    book = owned_book(session, owner, book_id)
    if book.character_generation_id is not None and not body.confirm_change:
        raise HTTPException(409, CHARACTER_SET)
    gen = owned_generation(session, owner, body.generation_id)  # 別人的與不存在一樣 404
    if gen.book_id != book_id:
        raise HTTPException(409, "這筆生成紀錄不屬於這本繪本")
    if gen.kind != GenKind.CHARACTER:
        raise HTTPException(409, "只能選擇角色候選圖")
    if gen.status != GenStatus.SUCCEEDED:
        raise HTTPException(409, "這張候選圖尚未成功生成")
    if gen.discarded:
        raise HTTPException(409, "這張候選圖已被捨棄")
    if gen.image_path is None:
        raise HTTPException(409, "這張候選圖已被清理")
    _select_character(session, book_id, gen.id, gen.user_text, body.confirm_change)
    session.refresh(book)
    return book_out(session, book)


@router.post("/{book_id}/character/upload", status_code=201, response_model=BookOut)
def upload_character(
    book_id: int,
    file: UploadFile,
    confirm_change: bool = Form(False),
    session: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
    owner: Owner = Depends(current_owner),
) -> BookOut:
    """「↑ 上傳自己的主角」：驗證同上傳背景，等比例縮放到角色尺寸、置中貼在白底（不去背），
    存成 engine="upload"、request_json.uploaded=true 的 CHARACTER 並設為角色。
    已設定角色時要 confirm_change=true（表單欄位），否則 409。不計當日生成次數。"""
    from app.api.compose import _mark_stored, read_upload  # compose 匯入本模組

    book = owned_book(session, owner, book_id)
    if book.character_generation_id is not None and not confirm_change:
        raise HTTPException(409, CHARACTER_SET)
    started = time.monotonic()
    width, height = CHARACTER_SIZE
    src, original_size = read_upload(file, (width, height))
    out, box = letterbox(src, (width, height))
    buf = io.BytesIO()
    out.save(buf, "PNG")
    request_json = encode_request(
        kind=GenKind.CHARACTER, prompt="", negative_prompt="", seed=0, width=width, height=height,
        extra={"source": "upload", "uploaded": True},
    )
    params = {"filename": file.filename, "original_size": list(original_size), "paste_box": list(box)}
    gen = Generation(
        book_id=book_id,
        kind=GenKind.CHARACTER,
        user_text="",
        prompt="",
        negative_prompt="",
        seed=0,
        width=width,
        height=height,
        request_json=request_json,
        engine=UPLOAD_ENGINE,
        workflow_name=None,
        models_json="[]",
        params_json=json.dumps(params, ensure_ascii=False, sort_keys=True),
        started_at=utcnow(),
        elapsed_s=time.monotonic() - started,
    )
    gen_id, rel = store_outside_tx(session, storage, gen, buf.getvalue())
    _mark_stored(session, storage, gen, rel, "上傳的主角剛被移除，請重新上傳")
    # 主角已經是一筆有圖的成功紀錄：之後設定失敗（例如同時被設定了別的角色）也不刪它的物件
    _select_character(session, book_id, gen_id, None, confirm_change)
    session.refresh(book)
    return book_out(session, book)
