"""generation 查詢、圖片、捨棄、取消與佇列狀態端點。"""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import case, literal
from sqlmodel import Session, col, func, select, update

from app.api.books import _book_or_404
from app.api.deps import get_session, get_storage, get_worker
from app.api.schemas import GenerationOut
from app.models import Book, Generation, GenKind, GenStatus, Page, utcnow
from app.services.worker import GenerationWorker, WorkerState
from app.storage import Storage

router = APIRouter(prefix="/api", tags=["generations"])


class GenerationUpdate(BaseModel):
    discarded: bool


class QueueStatus(BaseModel):
    state: WorkerState
    last_error: str | None
    queued: int
    running_id: int | None


def _gen_or_404(session: Session, gen_id: int) -> Generation:
    gen = session.get(Generation, gen_id)
    if gen is None:
        raise HTTPException(404, "找不到這筆生成紀錄")
    return gen


def _out(gen: Generation, worker: GenerationWorker) -> GenerationOut:
    return GenerationOut.from_generation(
        gen, queue_position=worker.queue_position(gen.id), progress=worker.progress.get(gen.id)
    )


@router.get("/generations/{gen_id}", response_model=GenerationOut)
def get_generation(
    gen_id: int,
    session: Session = Depends(get_session),
    worker: GenerationWorker = Depends(get_worker),
) -> GenerationOut:
    return _out(_gen_or_404(session, gen_id), worker)


@router.get("/generations/{gen_id}/image")
def get_generation_image(
    gen_id: int,
    session: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
) -> FileResponse:
    gen = _gen_or_404(session, gen_id)
    if gen.status != GenStatus.SUCCEEDED or not gen.image_path:
        raise HTTPException(404, "這筆生成紀錄還沒有圖片")
    path = storage.abs(gen.image_path)
    if not path.is_file():
        raise HTTPException(404, "圖片檔案遺失")
    return FileResponse(path, media_type="image/png")


@router.get("/generations/{gen_id}/mask")
def get_generation_mask(
    gen_id: int,
    session: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
) -> FileResponse:
    gen = _gen_or_404(session, gen_id)
    if not gen.mask_path:
        raise HTTPException(404, "這筆生成紀錄沒有遮罩")
    path = storage.abs(gen.mask_path)
    if not path.is_file():
        raise HTTPException(404, "遮罩檔案遺失")
    return FileResponse(path, media_type="image/png")


@router.get("/books/{book_id}/generations", response_model=list[GenerationOut])
def list_generations(
    book_id: int,
    kind: GenKind | None = None,
    page_id: int | None = None,
    include_discarded: bool = False,
    session: Session = Depends(get_session),
    worker: GenerationWorker = Depends(get_worker),
) -> list[GenerationOut]:
    _book_or_404(session, book_id)
    stmt = select(Generation).where(col(Generation.book_id) == book_id)
    if kind is not None:
        stmt = stmt.where(col(Generation.kind) == kind)
    if page_id is not None:
        stmt = stmt.where(col(Generation.page_id) == page_id)
    if not include_discarded:
        stmt = stmt.where(col(Generation.discarded).is_(False))
    gens = session.exec(stmt.order_by(col(Generation.id).desc())).all()
    return [_out(g, worker) for g in gens]


@router.patch("/generations/{gen_id}", response_model=GenerationOut)
def update_generation(
    gen_id: int,
    body: GenerationUpdate,
    session: Session = Depends(get_session),
    worker: GenerationWorker = Depends(get_worker),
) -> GenerationOut:
    gen = _gen_or_404(session, gen_id)
    guard = col(Generation.id) == gen_id
    if body.discarded:
        in_use = (
            select(Page.id).where(
                (col(Page.selected_generation_id) == gen_id)
                | (col(Page.background_generation_id) == gen_id)
                | (col(Page.pose_generation_id) == gen_id)
            ).exists()
            | select(Book.id).where(col(Book.character_generation_id) == gen_id).exists()
        )
        guard = guard & ~in_use
    values: dict = {"discarded": body.discarded}
    if body.discarded:
        # 捨棄排隊中的任務等於取消它，避免 worker 之後仍去執行
        values["status"] = case(
            (
                col(Generation.status) == GenStatus.QUEUED,
                literal(GenStatus.CANCELLED, Generation.__table__.c.status.type),
            ),
            else_=col(Generation.status),
        )
        values["finished_at"] = case(
            (col(Generation.status) == GenStatus.QUEUED, utcnow()),
            else_=col(Generation.finished_at),
        )
    res = session.exec(update(Generation).where(guard).values(**values))
    session.commit()
    if res.rowcount == 0:
        raise HTTPException(409, "這筆生成紀錄正被頁面使用或作為角色，無法捨棄")
    session.refresh(gen)
    return _out(gen, worker)


@router.post("/generations/{gen_id}/cancel", response_model=GenerationOut)
def cancel_generation(
    gen_id: int,
    session: Session = Depends(get_session),
    worker: GenerationWorker = Depends(get_worker),
) -> GenerationOut:
    gen = _gen_or_404(session, gen_id)
    res = session.exec(
        update(Generation)
        .where(col(Generation.id) == gen_id, col(Generation.status) == GenStatus.QUEUED)
        .values(status=GenStatus.CANCELLED, finished_at=utcnow())
    )
    session.commit()
    if res.rowcount == 0:
        raise HTTPException(409, "只有排隊中的任務可以取消")
    session.refresh(gen)
    return _out(gen, worker)


@router.get("/system/queue", response_model=QueueStatus)
def queue_status(
    session: Session = Depends(get_session), worker: GenerationWorker = Depends(get_worker)
) -> QueueStatus:
    queued = session.exec(
        select(func.count()).select_from(Generation).where(col(Generation.status) == GenStatus.QUEUED)
    ).one()
    running_id = session.exec(
        select(Generation.id).where(col(Generation.status) == GenStatus.RUNNING).limit(1)
    ).first()
    return QueueStatus(
        state=worker.state, last_error=worker.last_error, queued=queued, running_id=running_id
    )
