"""頁面合成流程：背景與角色動作的生成／選用、位置草稿、確認合成。

合成圖寫成一筆 `composite` generation 並設為頁面的 selected_generation_id，
所以線稿、修線圖層（以 generation 為 key）、QA、匯出都沿用既有流程。
"""

import io
import json
import random
import time

from fastapi import APIRouter, Depends, HTTPException
from PIL import Image
from sqlalchemy import and_, case, null
from sqlmodel import Session, col, select, update

from app.api.books import MAX_SEED, _book_or_404
from app.api.deps import get_session, get_storage, get_worker
from app.api.pages import _page_or_404, page_out, reset_review_when
from app.api.schemas import GenerationOut, PageOut, PagePlacement, PoseCreate, SelectGeneration
from app.models import Generation, GenKind, GenStatus, Page, utcnow
from app.services.compose import (
    COMPOSE_VERSION,
    Placement,
    compose,
    composite_matches,
    page_placement,
)
from app.services.layout import compute_layout
from app.services.prompts import (
    BACKGROUND_NEGATIVE,
    POSE_NEGATIVE,
    POSE_SIZE,
    background_prompt,
    pose_prompt,
)
from app.services.worker import GenerationWorker, encode_request
from app.storage import Storage

router = APIRouter(prefix="/api", tags=["compose"])

NEED_BACKGROUND = "請先選好背景"
NEED_POSE = "請先選好角色動作"
FILE_MISSING = "圖片檔案遺失，請重新產生"
INPUTS_CHANGED = "背景、角色動作或位置剛被更改，請重新確認合成"

_LABEL = {GenKind.BACKGROUND: "背景", GenKind.POSE: "角色動作"}
_PAGE_FIELD = {GenKind.BACKGROUND: "background_generation_id", GenKind.POSE: "pose_generation_id"}


def _out(gen: Generation, worker: GenerationWorker) -> GenerationOut:
    return GenerationOut.from_generation(
        gen, queue_position=worker.queue_position(gen.id), progress=worker.progress.get(gen.id)
    )


def _usable_conditions(kind: GenKind) -> list:
    """背景／角色動作可以選用、合成的條件（SQL）；角色動作還要有遮罩。"""
    conds = [
        col(Generation.kind) == kind,
        col(Generation.status) == GenStatus.SUCCEEDED,
        col(Generation.discarded).is_(False),
        col(Generation.image_path).is_not(None),
    ]
    if kind == GenKind.POSE:
        conds.append(col(Generation.mask_path).is_not(None))
    return conds


def _is_usable(gen: Generation | None, kind: GenKind, page_id: int) -> bool:
    return (
        gen is not None
        and gen.page_id == page_id
        and gen.kind == kind
        and gen.status == GenStatus.SUCCEEDED
        and not gen.discarded
        and bool(gen.image_path)
        and (kind != GenKind.POSE or bool(gen.mask_path))
    )


def _enqueue(
    session: Session, storage: Storage, worker: GenerationWorker, gen: Generation, **request
) -> GenerationOut:
    """新增一筆排隊中的 generation；sketch_bytes 有值時另存一份快照給它用。"""
    sketch_bytes: bytes | None = request.pop("sketch_bytes", None)
    written: str | None = None
    try:
        session.add(gen)
        session.flush()
        snapshot = None
        if sketch_bytes is not None:
            snapshot = storage.sketch_snapshot(gen.book_id, gen.page_id, gen.id)
            storage.write(snapshot, sketch_bytes)
            written = snapshot
        gen.request_json = encode_request(
            kind=gen.kind,
            prompt=gen.prompt,
            negative_prompt=gen.negative_prompt,
            seed=gen.seed,
            width=gen.width,
            height=gen.height,
            structure_image=snapshot,
            **request,
        )
        session.commit()
    except Exception:
        session.rollback()
        if written:
            storage.delete(written)
        raise
    worker.notify()
    return _out(gen, worker)


@router.post("/pages/{page_id}/backgrounds", status_code=202, response_model=GenerationOut)
def create_background(
    page_id: int,
    session: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
    worker: GenerationWorker = Depends(get_worker),
) -> GenerationOut:
    """產生一張背景：場景描述＋（有的話）構圖草圖；不使用角色參考圖。"""
    page = _page_or_404(session, page_id)
    book = _book_or_404(session, page.book_id)
    sketch_bytes: bytes | None = None
    if page.sketch_path:
        try:
            sketch_bytes = storage.abs(page.sketch_path).read_bytes()
        except (FileNotFoundError, ValueError) as e:
            raise HTTPException(409, "草圖檔案遺失，請重新上傳") from e
    width, height = compute_layout(book.paper_size, book.print_mode).generation_size
    gen = Generation(
        book_id=book.id,
        page_id=page.id,
        kind=GenKind.BACKGROUND,
        user_text=page.scene_prompt,
        prompt=background_prompt(page.scene_prompt),
        negative_prompt=BACKGROUND_NEGATIVE,
        seed=random.randint(0, MAX_SEED),
        width=width,
        height=height,
        request_json="",
    )
    return _enqueue(
        session, storage, worker, gen,
        sketch_bytes=sketch_bytes, structure_strength=page.controlnet_strength,
    )


@router.post("/pages/{page_id}/poses", status_code=202, response_model=GenerationOut)
def create_pose(
    page_id: int,
    body: PoseCreate,
    session: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
    worker: GenerationWorker = Depends(get_worker),
) -> GenerationOut:
    """產生一個角色動作（白底、自動去背）：角色描述＋動作文字，不帶場景描述；尺寸固定 POSE_SIZE。"""
    page = _page_or_404(session, page_id)
    book = _book_or_404(session, page.book_id)
    char = (
        session.get(Generation, book.character_generation_id)
        if book.character_generation_id is not None
        else None
    )
    if char is None or not char.image_path:
        raise HTTPException(409, "請先設定角色")
    width, height = POSE_SIZE
    gen = Generation(
        book_id=book.id,
        page_id=page.id,
        kind=GenKind.POSE,
        user_text=body.action,
        prompt=pose_prompt(book.character_description or "", body.action),
        negative_prompt=POSE_NEGATIVE,
        seed=random.randint(0, MAX_SEED),
        width=width,
        height=height,
        request_json="",
    )
    return _enqueue(session, storage, worker, gen, character_image=char.image_path)


def _pick(session: Session, page_id: int, gen_id: int, kind: GenKind) -> PageOut:
    label = _LABEL[kind]
    page = _page_or_404(session, page_id)
    gen = session.get(Generation, gen_id)
    if gen is None:
        raise HTTPException(404, "找不到這筆生成紀錄")
    if gen.page_id != page_id:
        raise HTTPException(409, "這筆生成紀錄不屬於這一頁")
    if gen.kind != kind:
        raise HTTPException(409, f"這筆生成紀錄不是{label}")
    if gen.status != GenStatus.SUCCEEDED:
        raise HTTPException(409, f"這張{label}尚未成功生成")
    if gen.discarded:
        raise HTTPException(409, f"這張{label}已被捨棄")
    if kind == GenKind.POSE and not gen.mask_path:
        raise HTTPException(409, "這個角色動作沒有去背遮罩，請重新產生")
    field = _PAGE_FIELD[kind]
    still_valid = (
        select(Generation.id)
        .where(col(Generation.id) == gen.id, col(Generation.page_id) == page_id,
               *_usable_conditions(kind))
        .exists()
    )
    # 換了不同的背景／角色動作（以寫入當下的舊值判斷）：選定圖若是合成圖就清空並退回草稿；
    # 舊流程的 page 圖不動，直到確認新的合成圖取代它
    selected_is_composite = (
        select(Generation.id)
        .where(col(Generation.id) == col(Page.selected_generation_id),
               col(Generation.kind) == GenKind.COMPOSITE)
        .correlate(Page)
        .exists()
    )
    clear = and_(getattr(Page, field).is_distinct_from(gen.id), selected_is_composite)
    res = session.exec(
        update(Page)
        .where(col(Page.id) == page_id, still_valid)
        .values(
            **{field: gen.id},
            updated_at=utcnow(),
            selected_generation_id=case((clear, null()), else_=col(Page.selected_generation_id)),
            **reset_review_when(clear),
        )
    )
    session.commit()
    if res.rowcount == 0:
        raise HTTPException(409, f"這張{label}已無法選用（可能剛被捨棄）")
    session.refresh(page)
    return page_out(session, page)


@router.post("/pages/{page_id}/background", response_model=PageOut)
def select_background(
    page_id: int, body: SelectGeneration, session: Session = Depends(get_session)
) -> PageOut:
    return _pick(session, page_id, body.generation_id, GenKind.BACKGROUND)


@router.post("/pages/{page_id}/pose", response_model=PageOut)
def select_pose(
    page_id: int, body: SelectGeneration, session: Session = Depends(get_session)
) -> PageOut:
    return _pick(session, page_id, body.generation_id, GenKind.POSE)


@router.put("/pages/{page_id}/placement", response_model=PageOut)
def save_placement(
    page_id: int, body: PagePlacement, session: Session = Depends(get_session)
) -> PageOut:
    """位置草稿（拖拉時自動存）；不產生合成圖、不改頁面狀態。"""
    page = _page_or_404(session, page_id)
    page.placement_json = json.dumps(body.to_placement().to_dict())
    page.updated_at = utcnow()
    session.add(page)
    session.commit()
    return page_out(session, page)


def _load_image(storage: Storage, rel: str | None) -> Image.Image:
    if not rel:
        raise FileNotFoundError(rel)
    with Image.open(storage.abs(rel)) as im:
        im.load()
        return im.copy()


def _composite_params(bg_id: int, pose_id: int, placement: Placement) -> dict:
    return {
        "background_generation_id": bg_id,
        "pose_generation_id": pose_id,
        "placement": placement.to_dict(),
    }


def _find_composite(
    session: Session, storage: Storage, page_id: int, bg_id: int, pose_id: int, placement: Placement
) -> Generation | None:
    """同背景＋角色動作＋位置（Placement.key()）、同合成版本且檔案還在的既有合成圖。"""
    rows = session.exec(
        select(Generation)
        .where(col(Generation.page_id) == page_id, col(Generation.kind) == GenKind.COMPOSITE,
               col(Generation.status) == GenStatus.SUCCEEDED, col(Generation.discarded).is_(False))
        .order_by(col(Generation.id).desc())
    ).all()
    for g in rows:
        if composite_matches(g, bg_id, pose_id, placement) and g.image_path:
            try:
                if storage.abs(g.image_path).is_file():
                    return g
            except ValueError:
                continue
    return None


@router.post("/pages/{page_id}/compose", response_model=PageOut)
def confirm_compose(
    page_id: int,
    session: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
) -> PageOut:
    """用頁面目前的背景、角色動作與位置（沒存過就用預設）產生合成圖並設為選定圖片。
    完全相同的既有合成圖直接選回（修線一起回來）；換了圖就退回草稿、清角色檢查。"""
    page = _page_or_404(session, page_id)
    bg = (session.get(Generation, page.background_generation_id)
          if page.background_generation_id is not None else None)
    if not _is_usable(bg, GenKind.BACKGROUND, page_id):
        raise HTTPException(409, NEED_BACKGROUND)
    pose = (session.get(Generation, page.pose_generation_id)
            if page.pose_generation_id is not None else None)
    if not _is_usable(pose, GenKind.POSE, page_id):
        raise HTTPException(409, NEED_POSE)
    placement = page_placement(page.placement_json)
    placement_json = page.placement_json

    written: str | None = None
    try:
        target = _find_composite(session, storage, page_id, bg.id, pose.id, placement)
        if target is None:
            started_at, t0 = utcnow(), time.monotonic()
            try:
                bg_img = _load_image(storage, bg.image_path)
                pose_img = _load_image(storage, pose.image_path)
                mask_img = _load_image(storage, pose.mask_path)
            except (OSError, ValueError) as e:  # 檔案不在、壞掉或路徑不合法
                raise HTTPException(409, FILE_MISSING) from e
            try:
                out = compose(bg_img, pose_img, mask_img, placement)
            except ValueError as e:  # 遮罩是空的、與角色圖尺寸不同
                raise HTTPException(409, f"無法合成：{e}，請重新產生角色動作") from e
            buf = io.BytesIO()
            out.save(buf, "PNG")
            params = json.dumps(_composite_params(bg.id, pose.id, placement), ensure_ascii=False,
                                sort_keys=True)
            target = Generation(
                book_id=page.book_id,
                page_id=page.id,
                kind=GenKind.COMPOSITE,
                status=GenStatus.SUCCEEDED,
                user_text="",
                prompt="",
                negative_prompt="",
                seed=0,
                width=out.width,
                height=out.height,
                request_json=params,
                engine="compose",
                workflow_name="compose",
                workflow_version=COMPOSE_VERSION,
                models_json="[]",
                params_json=params,
                started_at=started_at,
            )
            session.add(target)
            session.flush()
            rel = storage.generation_image(page.book_id, target.id)
            storage.write(rel, buf.getvalue())
            written = rel
            target.image_path = rel
            target.elapsed_s = time.monotonic() - t0
            target.finished_at = utcnow()
            session.flush()
        target_id = target.id
        target_valid = (
            select(Generation.id)
            .where(col(Generation.id) == target_id, col(Generation.page_id) == page_id,
                   col(Generation.kind) == GenKind.COMPOSITE,
                   col(Generation.status) == GenStatus.SUCCEEDED,
                   col(Generation.discarded).is_(False))
            .exists()
        )
        changed = col(Page.selected_generation_id).is_distinct_from(target_id)
        # 讀取後若背景／角色動作／位置被改（例如另一個分頁），不要用舊的輸入蓋掉
        res = session.exec(
            update(Page)
            .where(
                col(Page.id) == page_id,
                col(Page.background_generation_id) == bg.id,
                col(Page.pose_generation_id) == pose.id,
                col(Page.placement_json).is_not_distinct_from(placement_json),
                target_valid,
            )
            .values(selected_generation_id=target_id, updated_at=utcnow(),
                    **reset_review_when(changed))
        )
        if res.rowcount == 0:
            raise HTTPException(409, INPUTS_CHANGED)
        session.commit()
    except Exception:
        session.rollback()
        if written:
            storage.delete(written)
        raise
    session.refresh(page)
    return page_out(session, page)
