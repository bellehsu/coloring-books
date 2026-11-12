"""generation 查詢、圖片、捨棄、取消與佇列狀態端點。"""

import io
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from PIL import Image
from pydantic import BaseModel
from sqlalchemy import case, literal
from sqlmodel import Session, col, func, select, update

from app.api.deps import current_owner, get_session, get_storage, get_worker, owned_book, owned_generation
from app.api.files import signed_redirect
from app.api.schemas import GenerationOut
from app.models import SQLITE_INT_MAX, SQLITE_INT_MIN, Book, Generation, GenKind, GenStatus, Owner, Page, utcnow
from app.services.comfy_engine import WORKFLOWS
from app.services.compose import cutout
from app.services.engine import ENGINE_UNREACHABLE_PUBLIC, public_engine_error
from app.services.usage import refund_generations
from app.services.worker import GenerationWorker, WorkerState, worker_job
from app.storage import Storage, StorageNotFound

router = APIRouter(prefix="/api", tags=["generations"])

IMAGE_CLEARED = "這張圖已被清理，無法還原"


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
    # 供狀態膠囊估算剩餘時間：排隊中／執行中（舊→新），與各類型最近成功的耗時（新→舊）。
    pending: list[PendingJob]
    recent: list[RecentJob]



def _queue_error(worker: GenerationWorker, owner: Owner) -> str | None:
    """管理者看原文；其他人只在引擎連不上時看到固定的中文訊息（不洩漏內部位址）。"""
    if owner.is_admin:
        return worker.last_error
    if worker.state == WorkerState.ENGINE_UNAVAILABLE:
        return ENGINE_UNREACHABLE_PUBLIC
    return None


def _out(gen: Generation, worker: GenerationWorker, owner: Owner) -> GenerationOut:
    return GenerationOut.from_generation(
        gen,
        queue_position=worker.queue_position(gen.id),
        progress=worker.progress.get(gen.id),
        is_admin=owner.is_admin,
    )


@router.get("/generations/{gen_id}", response_model=GenerationOut)
def get_generation(
    gen_id: int,
    session: Session = Depends(get_session),
    worker: GenerationWorker = Depends(get_worker),
    owner: Owner = Depends(current_owner),
) -> GenerationOut:
    return _out(owned_generation(session, owner, gen_id), worker, owner)


@router.get("/generations/{gen_id}/image")
def get_generation_image(
    request: Request,
    gen_id: int,
    session: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
    owner: Owner = Depends(current_owner),
) -> Response:
    gen = owned_generation(session, owner, gen_id)
    if gen.status != GenStatus.SUCCEEDED or not gen.image_path:
        raise HTTPException(404, "這筆生成紀錄還沒有圖片")
    try:
        found = storage.exists(gen.image_path)  # HEAD 就夠，不下載；不存在回 404 而不是轉到死網址
    except ValueError:
        found = False
    if not found:
        raise HTTPException(404, "圖片檔案遺失")
    return signed_redirect(request, storage, gen.image_path)


@router.get("/generations/{gen_id}/cutout")
def get_generation_cutout(
    gen_id: int,
    session: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
    owner: Owner = Depends(current_owner),
) -> Response:
    """角色動作的去背圖：裁到遮罩外接框的 RGBA PNG（alpha＝遮罩）。"""
    gen = owned_generation(session, owner, gen_id)
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
    page_id: Annotated[int | None, Query(ge=SQLITE_INT_MIN, le=SQLITE_INT_MAX)] = None,
    include_discarded: bool = False,
    session: Session = Depends(get_session),
    worker: GenerationWorker = Depends(get_worker),
    owner: Owner = Depends(current_owner),
) -> list[GenerationOut]:
    owned_book(session, owner, book_id)
    stmt = select(Generation).where(col(Generation.book_id) == book_id)
    if kind is not None:
        stmt = stmt.where(col(Generation.kind) == kind)
    if page_id is not None:
        stmt = stmt.where(col(Generation.page_id) == page_id)
    if not include_discarded:
        stmt = stmt.where(col(Generation.discarded).is_(False))
    gens = session.exec(stmt.order_by(col(Generation.id).desc())).all()
    return [_out(g, worker, owner) for g in gens]


@router.patch("/generations/{gen_id}", response_model=GenerationOut)
def update_generation(
    gen_id: int,
    body: GenerationUpdate,
    session: Session = Depends(get_session),
    worker: GenerationWorker = Depends(get_worker),
    owner: Owner = Depends(current_owner),
) -> GenerationOut:
    gen = owned_generation(session, owner, gen_id)
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
    else:
        # storage_gc 已清掉圖（成功、沒有圖）的不能還原：還原後可能被選成角色，之後的任務都拿不到圖
        cleared = (col(Generation.status) == GenStatus.SUCCEEDED) & col(Generation.image_path).is_(None)
        guard = guard & ~cleared
    # 保留期限（storage_gc）從 discarded_at 起算：捨棄時記下（已捨棄的不重算，期限不往後延），還原時清掉
    discarded_at_type = Generation.__table__.c.discarded_at.type
    values: dict = {
        "discarded": body.discarded,
        "discarded_at": (
            func.coalesce(col(Generation.discarded_at), literal(utcnow(), discarded_at_type))
            if body.discarded
            else None
        ),
    }
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
    if body.discarded and res.rowcount:
        refund_generations(session, [gen_id])  # 排隊中被取消的退回額度（已退過的不再退）
    session.commit()
    if res.rowcount == 0:
        if not body.discarded:
            raise HTTPException(409, IMAGE_CLEARED)
        raise HTTPException(409, "這筆生成紀錄正被頁面使用或作為角色，無法捨棄")
    session.refresh(gen)
    return _out(gen, worker, owner)


@router.post("/generations/{gen_id}/cancel", response_model=GenerationOut)
def cancel_generation(
    gen_id: int,
    session: Session = Depends(get_session),
    worker: GenerationWorker = Depends(get_worker),
    owner: Owner = Depends(current_owner),
) -> GenerationOut:
    gen = owned_generation(session, owner, gen_id)
    res = session.exec(
        update(Generation)
        .where(col(Generation.id) == gen_id, col(Generation.status) == GenStatus.QUEUED)
        .values(status=GenStatus.CANCELLED, finished_at=utcnow())
    )
    if res.rowcount:
        refund_generations(session, [gen_id])
    session.commit()
    if res.rowcount == 0:
        raise HTTPException(409, "只有排隊中的任務可以取消")
    session.refresh(gen)
    return _out(gen, worker, owner)


@router.get("/system/queue", response_model=QueueStatus)
def queue_status(
    session: Session = Depends(get_session),
    worker: GenerationWorker = Depends(get_worker),
    owner: Owner = Depends(current_owner),
) -> QueueStatus:
    """全站共用一張 GPU 的佇列：只回數量與估時用的類型／耗時，不含任何人的內容。
    非 admin 看不到任務失敗訊息（可能來自別人的任務）；引擎無法連線的訊息是系統狀態，照常回。"""
    queued = session.exec(
        select(func.count()).select_from(Generation).where(col(Generation.status) == GenStatus.QUEUED)
    ).one()
    pending = session.exec(
        select(Generation.kind)
        .where(
            col(Generation.status).in_([GenStatus.QUEUED, GenStatus.RUNNING]),
            col(Generation.kind).in_(_ETA_KINDS),
            worker_job(),  # 寫到一半的上傳背景不是 GPU 任務
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
    return QueueStatus(
        state=worker.state,
        last_error=_queue_error(worker, owner),
        queued=queued,
        pending=[PendingJob(kind=k.value) for k in pending],
        recent=recent,
    )
