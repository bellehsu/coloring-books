"""generation 查詢、圖片、捨棄、取消與佇列狀態端點。"""

import io
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from PIL import Image
from pydantic import BaseModel
from sqlalchemy import case, literal
from sqlmodel import Session, col, func, select, update

from app.api.books import _book_or_404
from app.api.deps import current_owner, get_session, get_storage, get_worker
from app.api.files import signed_redirect
from app.api.schemas import GenerationOut
from app.models import Book, Generation, GenKind, GenStatus, Owner, Page, utcnow
from app.services.comfy_engine import WORKFLOWS
from app.services.compose import cutout
from app.services.worker import GenerationWorker, WorkerState
from app.storage import Storage, StorageNotFound

router = APIRouter(prefix="/api", tags=["generations"])


class GenerationUpdate(BaseModel):
    discarded: bool


_ETA_KINDS = (GenKind.CHARACTER, GenKind.BACKGROUND, GenKind.POSE)
_RECENT_PER_KIND = 10


class PendingJob(BaseModel):
    kind: Literal["character", "background", "pose"]


class RecentJob(BaseModel):
    kind: Literal["character", "background", "pose"]
    elapsed_s: float


class QueueStatus(BaseModel):
    state: WorkerState
    last_error: str | None
    queued: int
    running_id: int | None
    # 供狀態膠囊估算剩餘時間：排隊中／執行中（舊→新），與各類型最近成功的耗時（新→舊）。
    pending: list[PendingJob]
    recent: list[RecentJob]


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
    request: Request,
    gen_id: int,
    session: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
) -> Response:
    gen = _gen_or_404(session, gen_id)
    if gen.status != GenStatus.SUCCEEDED or not gen.image_path:
        raise HTTPException(404, "這筆生成紀錄還沒有圖片")
    try:
        found = storage.exists(gen.image_path)  # HEAD 就夠，不下載；不存在回 404 而不是轉到死網址
    except ValueError:
        found = False
    if not found:
        raise HTTPException(404, "圖片檔案遺失")
    return signed_redirect(request, storage, gen.image_path)


@router.get("/generations/{gen_id}/mask")
def get_generation_mask(
    request: Request,
    gen_id: int,
    session: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
) -> Response:
    gen = _gen_or_404(session, gen_id)
    if not gen.mask_path:
        raise HTTPException(404, "這筆生成紀錄沒有遮罩")
    try:
        found = storage.exists(gen.mask_path)
    except ValueError:
        found = False
    if not found:
        raise HTTPException(404, "遮罩檔案遺失")
    return signed_redirect(request, storage, gen.mask_path)


@router.get("/generations/{gen_id}/cutout")
def get_generation_cutout(
    gen_id: int,
    session: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
) -> Response:
    """角色動作的去背圖：裁到遮罩外接框的 RGBA PNG（alpha＝遮罩）。"""
    gen = _gen_or_404(session, gen_id)
    if gen.kind != GenKind.POSE or not gen.mask_path or not gen.image_path:
        raise HTTPException(404, "這筆生成紀錄沒有去背圖")
    try:
        pose_png, mask_png = storage.get(gen.image_path), storage.get(gen.mask_path)
    except StorageNotFound:  # 物件不見了：與其他圖片端點一致
        raise HTTPException(404, "圖片檔案遺失") from None
    except ValueError:  # key 不合法
        raise HTTPException(409, "圖片檔案遺失，請重新產生") from None
    try:
        with Image.open(io.BytesIO(pose_png)) as pose, Image.open(io.BytesIO(mask_png)) as mask:
            out = cutout(pose, mask)
    except (OSError, ValueError):  # 物件在但讀不出圖、或遮罩與圖對不上
        raise HTTPException(409, "圖片檔案遺失，請重新產生") from None
    buf = io.BytesIO()
    out.save(buf, format="PNG")
    # 同一個 generation id 的去背圖不會變（圖與遮罩都不再改寫）
    return Response(content=buf.getvalue(), media_type="image/png",
                    headers={"Cache-Control": "private, max-age=86400"})


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
    session: Session = Depends(get_session),
    worker: GenerationWorker = Depends(get_worker),
    owner: Owner = Depends(current_owner),
) -> QueueStatus:
    """全站共用一張 GPU 的佇列：只回數量與估時用的類型／耗時，不含任何人的內容。
    非 admin 看不到執行中的 id 與任務失敗訊息（可能來自別人的任務）；引擎無法連線的訊息是系統狀態，照常回。"""
    queued = session.exec(
        select(func.count()).select_from(Generation).where(col(Generation.status) == GenStatus.QUEUED)
    ).one()
    running_id = session.exec(
        select(Generation.id).where(col(Generation.status) == GenStatus.RUNNING).limit(1)
    ).first()
    pending = session.exec(
        select(Generation.kind)
        .where(
            col(Generation.status).in_([GenStatus.QUEUED, GenStatus.RUNNING]),
            col(Generation.kind).in_(_ETA_KINDS),
        )
        .order_by(col(Generation.created_at), col(Generation.id))
    ).all()
    recent: list[RecentJob] = []
    for kind in _ETA_KINDS:
        rows = session.exec(
            select(Generation.elapsed_s)
            .where(
                col(Generation.status) == GenStatus.SUCCEEDED,
                col(Generation.kind) == kind,
                col(Generation.elapsed_s).is_not(None),
                # 只算目前的 Qwen workflow：上傳（耗時約 0）與舊 SDXL 列會把估時拉低
                col(Generation.engine) == "comfyui",
                col(Generation.workflow_name).in_(list(WORKFLOWS.values())),
            )
            .order_by(col(Generation.created_at).desc(), col(Generation.id).desc())
            .limit(_RECENT_PER_KIND)
        ).all()
        recent += [RecentJob(kind=kind.value, elapsed_s=e) for e in rows]
    system_error = worker.state == WorkerState.ENGINE_UNAVAILABLE
    return QueueStatus(
        state=worker.state,
        last_error=worker.last_error if owner.is_admin or system_error else None,
        queued=queued,
        running_id=running_id if owner.is_admin else None,
        pending=[PendingJob(kind=k.value) for k in pending],
        recent=recent,
    )
