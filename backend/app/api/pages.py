"""頁面、草圖、頁面候選與選定端點。"""

import random

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlmodel import Session, col, func, select, update

from app.api.books import MAX_SEED, _book_or_404
from app.api.deps import get_session, get_storage, get_worker
from app.api.schemas import (
    GenerationOut,
    PageCandidatesCreate,
    PageCreate,
    PageOrder,
    PageOut,
    PageUpdate,
    SelectGeneration,
)
from app.models import Generation, GenKind, GenStatus, Page, utcnow
from app.services.layout import compute_layout
from app.services.prompts import PAGE_NEGATIVE, page_prompt
from app.services.worker import GenerationWorker, encode_request
from app.storage import ImageRejected, Storage, normalize_png

router = APIRouter(prefix="/api", tags=["pages"])

MAX_SKETCH_BYTES = 10_000_000


def _page_or_404(session: Session, page_id: int) -> Page:
    page = session.get(Page, page_id)
    if page is None:
        raise HTTPException(404, "找不到這一頁")
    return page


@router.post("/books/{book_id}/pages", status_code=201, response_model=PageOut)
def create_page(
    book_id: int, body: PageCreate, session: Session = Depends(get_session)
) -> PageOut:
    _book_or_404(session, book_id)
    top = session.exec(select(func.max(Page.position)).where(Page.book_id == book_id)).one()
    page = Page(book_id=book_id, position=0 if top is None else top + 1, scene_prompt=body.scene_prompt)
    session.add(page)
    session.commit()
    return PageOut.from_page(page)


@router.get("/books/{book_id}/pages", response_model=list[PageOut])
def list_pages(book_id: int, session: Session = Depends(get_session)) -> list[PageOut]:
    _book_or_404(session, book_id)
    pages = session.exec(
        select(Page).where(Page.book_id == book_id).order_by(col(Page.position), col(Page.id))
    ).all()
    return [PageOut.from_page(p) for p in pages]


@router.post("/books/{book_id}/pages/order", response_model=list[PageOut])
def reorder_pages(
    book_id: int, body: PageOrder, session: Session = Depends(get_session)
) -> list[PageOut]:
    _book_or_404(session, book_id)
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
    return [PageOut.from_page(pages[pid]) for pid in body.page_ids]


@router.patch("/pages/{page_id}", response_model=PageOut)
def update_page(
    page_id: int, body: PageUpdate, session: Session = Depends(get_session)
) -> PageOut:
    page = _page_or_404(session, page_id)
    if body.scene_prompt is not None:
        page.scene_prompt = body.scene_prompt
    if body.controlnet_strength is not None:
        page.controlnet_strength = body.controlnet_strength
    page.updated_at = utcnow()
    session.add(page)
    session.commit()
    return PageOut.from_page(page)


@router.put("/pages/{page_id}/sketch", response_model=PageOut)
def upload_sketch(
    page_id: int,
    file: UploadFile,
    session: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
) -> PageOut:
    page = _page_or_404(session, page_id)
    data = file.file.read(MAX_SKETCH_BYTES + 1)
    if len(data) > MAX_SKETCH_BYTES:
        raise HTTPException(400, f"檔案過大，上限 {MAX_SKETCH_BYTES} bytes")
    try:
        png, _ = normalize_png(data, max_bytes=MAX_SKETCH_BYTES)
    except ImageRejected as e:
        raise HTTPException(400, str(e)) from e
    rel = storage.sketch(page.book_id, page.id)
    storage.write(rel, png)
    page.sketch_path = rel
    page.updated_at = utcnow()
    session.add(page)
    session.commit()
    return PageOut.from_page(page)


@router.delete("/pages/{page_id}/sketch", response_model=PageOut)
def delete_sketch(
    page_id: int,
    session: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
) -> PageOut:
    page = _page_or_404(session, page_id)
    old = page.sketch_path
    page.sketch_path = None
    page.updated_at = utcnow()
    session.add(page)
    session.commit()
    if old:
        storage.delete(old)
    return PageOut.from_page(page)


@router.get("/pages/{page_id}/sketch")
def get_sketch(
    page_id: int,
    session: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
) -> FileResponse:
    page = _page_or_404(session, page_id)
    if not page.sketch_path or not storage.abs(page.sketch_path).is_file():
        raise HTTPException(404, "這一頁沒有草圖")
    return FileResponse(storage.abs(page.sketch_path), media_type="image/png")


@router.post("/pages/{page_id}/candidates", status_code=202, response_model=list[GenerationOut])
def create_page_candidates(
    page_id: int,
    body: PageCandidatesCreate,
    session: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
    worker: GenerationWorker = Depends(get_worker),
) -> list[GenerationOut]:
    page = _page_or_404(session, page_id)
    book = _book_or_404(session, page.book_id)
    char = (
        session.get(Generation, book.character_generation_id)
        if book.character_generation_id is not None
        else None
    )
    if char is None or not char.image_path:
        raise HTTPException(409, "請先設定角色")
    if body.seed is not None and body.seed + body.count - 1 > MAX_SEED:
        raise HTTPException(
            422, f"seed 加上候選數量不可超過 {MAX_SEED}（seed 最大為 {MAX_SEED - body.count + 1}）"
        )
    base = body.seed if body.seed is not None else random.randint(0, MAX_SEED + 1 - body.count)
    prompt = page_prompt(book.character_description or "", page.scene_prompt)
    width, height = compute_layout(book.paper_size, book.print_mode).generation_size
    sketch_bytes: bytes | None = None
    if page.sketch_path:
        try:
            sketch_bytes = storage.abs(page.sketch_path).read_bytes()
        except (FileNotFoundError, ValueError) as e:
            raise HTTPException(409, "草圖檔案遺失，請重新上傳") from e
    gens = [
        Generation(
            book_id=book.id,
            page_id=page.id,
            kind=GenKind.PAGE,
            user_text=page.scene_prompt,
            prompt=prompt,
            negative_prompt=PAGE_NEGATIVE,
            seed=base + i,
            width=width,
            height=height,
            request_json="",
        )
        for i in range(body.count)
    ]
    written: list[str] = []
    try:
        session.add_all(gens)
        session.flush()
        for g in gens:
            snapshot = None
            if sketch_bytes is not None:
                snapshot = storage.sketch_snapshot(book.id, page.id, g.id)
                storage.write(snapshot, sketch_bytes)
                written.append(snapshot)
            g.request_json = encode_request(
                kind=GenKind.PAGE,
                prompt=prompt,
                negative_prompt=PAGE_NEGATIVE,
                seed=g.seed,
                width=width,
                height=height,
                character_image=char.image_path,
                structure_image=snapshot,
                structure_strength=page.controlnet_strength,
            )
        session.commit()
    except Exception:
        session.rollback()
        for rel in written:
            storage.delete(rel)
        raise
    worker.notify()
    return [
        GenerationOut.from_generation(
            g, queue_position=worker.queue_position(g.id), progress=worker.progress.get(g.id)
        )
        for g in gens
    ]


@router.post("/pages/{page_id}/select", response_model=PageOut)
def select_generation(
    page_id: int, body: SelectGeneration, session: Session = Depends(get_session)
) -> PageOut:
    page = _page_or_404(session, page_id)
    gen = session.get(Generation, body.generation_id)
    if gen is None:
        raise HTTPException(404, "找不到這筆生成紀錄")
    if gen.page_id != page_id:
        raise HTTPException(409, "這筆生成紀錄不屬於這一頁")
    if gen.kind != GenKind.PAGE:
        raise HTTPException(409, "只能選擇頁面候選圖")
    if gen.status != GenStatus.SUCCEEDED:
        raise HTTPException(409, "這張候選圖尚未成功生成")
    if gen.discarded:
        raise HTTPException(409, "這張候選圖已被捨棄")
    still_valid = (
        select(Generation.id)
        .where(
            col(Generation.id) == gen.id,
            col(Generation.page_id) == page_id,
            col(Generation.kind) == GenKind.PAGE,
            col(Generation.status) == GenStatus.SUCCEEDED,
            col(Generation.discarded).is_(False),
        )
        .exists()
    )
    res = session.exec(
        update(Page)
        .where(col(Page.id) == page_id, still_valid)
        .values(selected_generation_id=gen.id, updated_at=utcnow())
    )
    session.commit()
    if res.rowcount == 0:
        raise HTTPException(409, "這張候選圖已無法選擇（可能剛被捨棄）")
    session.refresh(page)
    return PageOut.from_page(page)
