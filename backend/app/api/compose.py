"""頁面合成流程：背景與角色動作的生成／選用、位置草稿、確認合成。

合成圖寫成一筆 `composite` generation 並設為頁面的 selected_generation_id，
所以線稿、修線圖層（以 generation 為 key）、QA、匯出都沿用既有流程。
"""

import io
import json
import logging
import random
import threading
import time

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from PIL import Image
from sqlalchemy import and_, case, null
from sqlmodel import Session, col, delete, select, update

from app.api.books import MAX_SEED
from app.api.deps import (
    current_owner,
    get_session,
    get_storage,
    get_worker,
    owned_book,
    owned_generation,
    owned_page,
)
from app.api.pages import page_out, reset_review_when
from app.api.schemas import (
    BackgroundCreate,
    BatchMaterialsIn,
    BatchMaterialsOut,
    BatchSkip,
    GenerationOut,
    PageOut,
    PagePlacement,
    PoseCreate,
    SelectGeneration,
)
from app.models import Book, Generation, GenKind, GenStatus, Owner, Page, utcnow
from app.services import usage
from app.services.compose import (
    COMPOSE_VERSION,
    Placement,
    compose,
    composite_matches,
    page_placement,
)
from app.services.layout import compute_layout
from app.services.prompts import (
    POSE_SIZE,
    pick_background_variation,
    pick_pose_variation,
    qwen_background_prompt,
    qwen_pose_prompt,
)
from app.services.worker import GenerationWorker, encode_request
from app.services.upload_bg import cover_crop
from app.storage import UPLOAD_MAX_PIXELS, ImageRejected, Storage, StorageNotFound, TooManyPixels, load_rgb

router = APIRouter(prefix="/api", tags=["compose"])
log = logging.getLogger(__name__)

NEED_BACKGROUND = "請先選好背景"
NEED_POSE = "請先選好角色動作"
FILE_MISSING = "圖片檔案遺失，請重新產生"
MAX_BG_UPLOAD_BYTES = 10_000_000
INPUTS_CHANGED = "背景、角色動作或位置剛被更改，請重新確認合成"

# 批次背景：後端是單一行程、handler 是同步的，用一把鎖序列化，兩個分頁同時按也不會重複排隊
_BATCH_LOCK = threading.Lock()

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


class _Job:
    """還沒加進 session 的排隊任務：generation 加上組 request_json 用的參數。"""

    def __init__(self, gen: Generation, *, character_image: str | None = None,
                 variation: str | None = None, extra: dict | None = None) -> None:
        self.gen = gen
        self.character_image = character_image
        self.variation = variation
        self.extra = extra or {}


def _add_queued(session: Session, job: _Job, usage_extra: dict) -> Generation:
    """把排隊任務加進目前的交易（不 commit）；request_json 由 gen 的欄位加上參數與計數紀錄組成。"""
    gen = job.gen
    session.add(gen)
    session.flush()
    gen.request_json = encode_request(
        kind=gen.kind,
        prompt=gen.prompt,
        negative_prompt=gen.negative_prompt,
        seed=gen.seed,
        width=gen.width,
        height=gen.height,
        character_image=job.character_image,
        variation=job.variation,
        extra={**job.extra, **usage_extra},
    )
    return gen


def _enqueue_jobs(
    session: Session, worker: GenerationWorker, owner: Owner, jobs: list[_Job]
) -> list[GenerationOut]:
    """同一個交易：預留 len(jobs) 次當日額度（不夠就 429，什麼都不排），再新增全部任務後一起 commit。
    沒有任務就不計數。"""
    if not jobs:
        return []
    try:
        day = usage.reserve(session, owner, len(jobs), usage.clock())
        extra = usage.usage_extra(owner, day)
        gens = [_add_queued(session, job, extra) for job in jobs]
        session.commit()
    except Exception:
        session.rollback()
        raise
    worker.notify()
    return [_out(gen, worker) for gen in gens]


def _last_simple_variation(session: Session, page_id: int) -> str | None:
    """這頁最近一張簡潔背景（依 id，不論狀態、是否丟棄）的構圖變化語；沒有回 None。
    再產生時排除它，連續兩張的構圖才會不同。"""
    for rj in session.exec(
        select(Generation.request_json)
        .where(Generation.page_id == page_id, Generation.kind == GenKind.BACKGROUND)
        .order_by(col(Generation.id).desc())
    ):
        try:
            d = json.loads(rj)
        except (TypeError, ValueError):
            continue
        if isinstance(d, dict) and d.get("background_style") == "simple":
            v = d.get("variation")
            return v if isinstance(v, str) else None
    return None


def _background_job(session: Session, book: Book, page: Page) -> _Job:
    """單張與批次共用：用頁面場景描述排一筆簡潔（Qwen）背景，負向空字串，不用草圖。
    構圖變化語排除這頁上一張簡潔背景用的。"""
    width, height = compute_layout(book.paper_size, book.print_mode).generation_size
    variation = pick_background_variation(
        random.Random(), exclude=_last_simple_variation(session, page.id)
    )
    gen = Generation(
        book_id=book.id,
        page_id=page.id,
        kind=GenKind.BACKGROUND,
        user_text=page.scene_prompt,
        prompt=qwen_background_prompt(page.scene_prompt, variation),
        negative_prompt="",
        seed=random.randint(0, MAX_SEED),
        width=width,
        height=height,
        request_json="",
    )
    return _Job(gen, variation=variation, extra={"background_style": "simple"})


@router.post("/pages/{page_id}/backgrounds", status_code=202, response_model=GenerationOut)
def create_background(
    page_id: int,
    body: BackgroundCreate | None = None,
    session: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
    worker: GenerationWorker = Depends(get_worker),
    owner: Owner = Depends(current_owner),
) -> GenerationOut:
    """產生一張背景：只用場景描述，不使用角色參考圖與草圖。body 可省略；舊前端送的 style 忽略。"""
    page = owned_page(session, owner, page_id)
    book = owned_book(session, owner, page.book_id)
    return _enqueue_jobs(session, worker, owner, [_background_job(session, book, page)])[0]


class _BookState:
    """批次判斷用：整本書每頁的背景／角色動作生成紀錄，以及選了舊流程整頁候選圖的頁面。"""

    def __init__(self, session: Session, book_id: int):
        self.pages = session.exec(
            select(Page).where(Page.book_id == book_id).order_by(col(Page.position), col(Page.id))
        ).all()
        self.gens: dict[GenKind, dict[int, list[Generation]]] = {
            GenKind.BACKGROUND: {}, GenKind.POSE: {}
        }
        for g in session.exec(
            select(Generation).where(
                Generation.book_id == book_id,
                col(Generation.kind).in_([GenKind.BACKGROUND, GenKind.POSE]),
            )
        ).all():
            if g.page_id is not None:
                self.gens[g.kind].setdefault(g.page_id, []).append(g)
        selected_ids = [
            p.selected_generation_id for p in self.pages if p.selected_generation_id is not None
        ]
        self.legacy_selected = set(
            session.exec(
                select(Generation.id).where(
                    col(Generation.id).in_(selected_ids), Generation.kind == GenKind.PAGE
                )
            ).all()
        ) if selected_ids else set()

    def skip_reason(self, page: Page, kind: GenKind) -> str | None:
        """該頁的這種素材為什麼不用排；要排回 None。"""
        bg = kind == GenKind.BACKGROUND
        if not (page.scene_prompt if bg else page.action_prompt).strip():
            return "沒有場景描述" if bg else "沒有動作描述"
        gens = self.gens[kind].get(page.id, [])
        slot = page.background_generation_id if bg else page.pose_generation_id
        if (
            slot is not None
            or page.selected_generation_id in self.legacy_selected  # 舊流程整頁候選圖
            or any(g.status == GenStatus.SUCCEEDED and not g.discarded for g in gens)
        ):
            return "已有背景" if bg else "已有角色動作"
        if any(g.status in (GenStatus.QUEUED, GenStatus.RUNNING) for g in gens):
            return "背景產生中" if bg else "角色動作產生中"
        return None


@router.post("/books/{book_id}/materials/batch", status_code=202, response_model=BatchMaterialsOut)
def create_materials_batch(
    book_id: int,
    body: BatchMaterialsIn | None = None,
    session: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
    worker: GenerationWorker = Depends(get_worker),
    owner: Owner = Depends(current_owner),
) -> BatchMaterialsOut:
    """「產生全部素材」：替整本書每一頁補排缺的背景與角色動作（依頁面順序，每頁先背景再動作）。
    略過的頁面回報 kind 與原因；body.upload_page_ids 的頁面改用上傳圖片，背景略過。
    _BATCH_LOCK 讓同時多個呼叫不會重複排隊。整批一次預留 N 次當日額度：不夠就 429，一筆都不排。"""
    upload_pages = set(body.upload_page_ids) if body else set()
    with _BATCH_LOCK:
        book = owned_book(session, owner, book_id)
        state = _BookState(session, book_id)
        char = (
            session.get(Generation, book.character_generation_id)
            if book.character_generation_id is not None
            else None
        )
        has_character = char is not None and bool(char.image_path)
        jobs: list[_Job] = []
        skipped: list[BatchSkip] = []
        for page in state.pages:
            if page.id in upload_pages:
                reason = "改用上傳圖片"
            else:
                reason = state.skip_reason(page, GenKind.BACKGROUND)
            if reason is not None:
                skipped.append(
                    BatchSkip(kind="background", page_id=page.id, position=page.position, reason=reason)
                )
            else:
                jobs.append(_background_job(session, book, page))
            reason = state.skip_reason(page, GenKind.POSE)
            if reason is None and not has_character:
                reason = "請先設定角色"
            if reason is not None:
                skipped.append(
                    BatchSkip(kind="pose", page_id=page.id, position=page.position, reason=reason)
                )
            else:
                jobs.append(_pose_job(session, book, page, page.action_prompt))
        queued = _enqueue_jobs(session, worker, owner, jobs)  # 整批一次預留，不夠就整批 429
        return BatchMaterialsOut(queued=queued, skipped=skipped)


def _pose_job(session: Session, book: Book, page: Page, action: str) -> _Job:
    """角色動作（Qwen）：以角色參考圖編輯出新動作；呼叫端負責確認有角色。"""
    char = session.get(Generation, book.character_generation_id)
    variation = pick_pose_variation(random.Random())
    width, height = POSE_SIZE
    gen = Generation(
        book_id=book.id,
        page_id=page.id,
        kind=GenKind.POSE,
        user_text=action,
        prompt=qwen_pose_prompt(action, variation),
        negative_prompt="",
        seed=random.randint(0, MAX_SEED),
        width=width,
        height=height,
        request_json="",
    )
    return _Job(gen, character_image=char.image_path, variation=variation, extra={"pose_mode": "precise"})


@router.post("/pages/{page_id}/poses", status_code=202, response_model=GenerationOut)
def create_pose(
    page_id: int,
    body: PoseCreate,
    session: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
    worker: GenerationWorker = Depends(get_worker),
    owner: Owner = Depends(current_owner),
) -> GenerationOut:
    """產生一個角色動作（白底、自動去背），尺寸固定 POSE_SIZE，不帶場景描述：以角色參考圖編輯出
    新動作，prompt 只有動作。舊前端送的 mode 忽略；不先檢查 preflight，缺東西時由生成失敗回報。"""
    page = owned_page(session, owner, page_id)
    book = owned_book(session, owner, page.book_id)
    char = (
        session.get(Generation, book.character_generation_id)
        if book.character_generation_id is not None
        else None
    )
    if char is None or not char.image_path:
        raise HTTPException(409, "請先設定角色")
    return _enqueue_jobs(session, worker, owner, [_pose_job(session, book, page, body.action)])[0]


def _pick(session: Session, owner: Owner, page_id: int, gen_id: int, kind: GenKind) -> PageOut:
    label = _LABEL[kind]
    page = owned_page(session, owner, page_id)
    gen = owned_generation(session, owner, gen_id)  # 別人的與不存在一樣 404
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
    page_id: int,
    body: SelectGeneration,
    session: Session = Depends(get_session),
    owner: Owner = Depends(current_owner),
) -> PageOut:
    return _pick(session, owner, page_id, body.generation_id, GenKind.BACKGROUND)


@router.post("/pages/{page_id}/backgrounds/upload", status_code=201, response_model=PageOut)
def upload_background(
    page_id: int,
    file: UploadFile,
    session: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
    owner: Owner = Depends(current_owner),
) -> PageOut:
    """上傳圖片直接當這頁的背景：置中裁切成書的生成尺寸，存成 engine="upload" 的背景並選用。"""
    page = owned_page(session, owner, page_id)
    book = owned_book(session, owner, page.book_id)
    started = time.monotonic()
    data = file.file.read(MAX_BG_UPLOAD_BYTES + 1)
    if len(data) > MAX_BG_UPLOAD_BYTES:
        raise HTTPException(400, f"檔案過大，上限 {MAX_BG_UPLOAD_BYTES} bytes")
    width, height = compute_layout(book.paper_size, book.print_mode).generation_size
    try:
        # JPEG 縮小解碼到約兩倍目標尺寸（LANCZOS 縮放仍有餘裕），不先整張轉成 PNG 再解回來
        src, original_size = load_rgb(
            data, max_bytes=MAX_BG_UPLOAD_BYTES, max_pixels=UPLOAD_MAX_PIXELS,
            draft_size=(2 * width, 2 * height),
        )
    except TooManyPixels as e:
        raise HTTPException(422, str(e)) from e
    except ImageRejected as e:
        raise HTTPException(400, str(e)) from e
    out, crop_box = cover_crop(src, (width, height))
    buf = io.BytesIO()
    out.save(buf, "PNG")
    elapsed_s = time.monotonic() - started
    request_json = encode_request(
        kind=GenKind.BACKGROUND, prompt="", negative_prompt="", seed=0, width=width, height=height,
        extra={"source": "upload"},
    )
    params = {"filename": file.filename, "original_size": list(original_size),
              "crop_box": list(crop_box)}
    gen = Generation(
        book_id=book.id,
        page_id=page.id,
        kind=GenKind.BACKGROUND,
        user_text="",
        prompt="",
        negative_prompt="",
        seed=0,
        width=width,
        height=height,
        request_json=request_json,
        engine="upload",
        workflow_name=None,
        models_json="[]",
        params_json=json.dumps(params, ensure_ascii=False, sort_keys=True),
        started_at=utcnow(),
        elapsed_s=elapsed_s,
    )
    gen_id, rel = _store_outside_tx(session, storage, gen, buf.getvalue())
    _mark_stored(session, storage, gen, rel, "上傳的背景剛被移除，請重新上傳")
    # 背景已經是一筆有圖的成功紀錄：之後選用失敗（例如剛被捨棄）也不刪它的物件
    return _pick(session, owner, page_id, gen_id, GenKind.BACKGROUND)


def _store_outside_tx(session: Session, storage: Storage, gen: Generation, data: bytes) -> tuple[int, str]:
    """把後端自己產生的圖（上傳背景、合成圖）寫進 storage，寫入期間不持有 SQLite 寫入鎖。

    R2 的 put 是網路請求（可能很慢）；在寫入交易裡做會讓其他寫入者等到 database is locked。所以：
    先以 RUNNING、沒有圖提交這一列（拿到 id 才有 key；storage_gc 把 RUNNING 當成寫入中，不刪它的物件），
    交易外寫物件，再由呼叫端以 _mark_stored／條件式 UPDATE 標成 SUCCEEDED 並記上 key。
    寫入失敗就刪掉暫存列再丟出原本的例外：列永遠不會指到不存在的物件（留下的物件由 storage_gc 清）。
    回傳 (id, key)。"""
    # 呼叫端在這之前只能有讀取（沒有未提交的寫入），這裡的 commit 只提交這一列
    gen.status = GenStatus.RUNNING
    gen.image_path = None
    session.add(gen)
    session.commit()
    rel = storage.generation_image(gen.book_id, gen.id)
    try:
        storage.put(rel, data)
    except Exception:
        _abandon(session, storage, gen.id, rel)
        raise
    return gen.id, rel


def _abandon(session: Session, storage: Storage, gen_id: int, rel: str) -> None:
    """刪掉還沒有圖的暫存列（只刪仍是 RUNNING、沒有 image_path 的），再盡力刪物件；失敗只記 log。"""
    session.rollback()
    try:
        session.exec(
            delete(Generation).where(col(Generation.id) == gen_id, col(Generation.status) == GenStatus.RUNNING,
                                     col(Generation.image_path).is_(None))
        )
        session.commit()
    except Exception:  # noqa: BLE001  刪不掉：列沒有圖，重啟時 requeue_interrupted 會把它標成失敗
        session.rollback()
        log.warning("刪除暫存的生成紀錄 %s 失敗", gen_id, exc_info=True)
    try:
        storage.delete(rel)
    except Exception:  # noqa: BLE001  留下的物件由 storage_gc 清
        log.warning("刪除物件 %s 失敗", rel, exc_info=True)


def _finish_stored(session: Session, gen_id: int, rel: str, **values):
    """條件式 UPDATE：暫存列（RUNNING、沒有圖）→ SUCCEEDED 並記上 key；不 commit，回傳結果（看 rowcount）。"""
    return session.exec(
        update(Generation)
        .where(col(Generation.id) == gen_id, col(Generation.status) == GenStatus.RUNNING,
               col(Generation.image_path).is_(None))
        .values(status=GenStatus.SUCCEEDED, image_path=rel, finished_at=utcnow(), **values)
    )


def _mark_stored(session: Session, storage: Storage, gen: Generation, rel: str, gone: str) -> None:
    """_finish_stored 並提交；暫存列已經不在（極少見）就刪物件、回 409。"""
    try:
        res = _finish_stored(session, gen.id, rel)
        session.commit()
    except Exception:
        _abandon(session, storage, gen.id, rel)
        raise
    if res.rowcount == 0:
        _abandon(session, storage, gen.id, rel)
        raise HTTPException(409, gone)
    session.refresh(gen)


@router.post("/pages/{page_id}/pose", response_model=PageOut)
def select_pose(
    page_id: int,
    body: SelectGeneration,
    session: Session = Depends(get_session),
    owner: Owner = Depends(current_owner),
) -> PageOut:
    return _pick(session, owner, page_id, body.generation_id, GenKind.POSE)


@router.put("/pages/{page_id}/placement", response_model=PageOut)
def save_placement(
    page_id: int,
    body: PagePlacement,
    session: Session = Depends(get_session),
    owner: Owner = Depends(current_owner),
) -> PageOut:
    """位置草稿（拖拉時自動存）；不產生合成圖、不改頁面狀態。"""
    page = owned_page(session, owner, page_id)
    page.placement_json = json.dumps(body.to_placement().to_dict())
    page.updated_at = utcnow()
    session.add(page)
    session.commit()
    return page_out(session, page)


def _load_image(storage: Storage, rel: str | None) -> Image.Image:
    """從 Storage 讀出圖片；key 是空的或物件不存在丟 FileNotFoundError。"""
    if not rel:
        raise FileNotFoundError(rel)
    try:
        data = storage.get(rel)
    except StorageNotFound:
        raise FileNotFoundError(rel) from None
    with Image.open(io.BytesIO(data)) as im:
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
    """同背景＋角色動作＋位置（Placement.key()）、同合成版本且物件還在的既有合成圖。
    只對符合條件的候選呼叫 storage.exists()（合成是 POST，不在每次顯示時走）。"""
    rows = session.exec(
        select(Generation)
        .where(col(Generation.page_id) == page_id, col(Generation.kind) == GenKind.COMPOSITE,
               col(Generation.status) == GenStatus.SUCCEEDED, col(Generation.discarded).is_(False))
        .order_by(col(Generation.id).desc())
    ).all()
    for g in rows:
        if composite_matches(g, bg_id, pose_id, placement) and g.image_path:
            try:
                if storage.exists(g.image_path):
                    return g
            except ValueError:
                continue
    return None


@router.post("/pages/{page_id}/compose", response_model=PageOut)
def confirm_compose(
    page_id: int,
    session: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
    owner: Owner = Depends(current_owner),
) -> PageOut:
    """用頁面目前的背景、角色動作與位置（沒存過就用預設）產生合成圖並設為選定圖片。
    完全相同的既有合成圖直接選回（修線一起回來）；換了圖就退回草稿、清角色檢查。"""
    page = owned_page(session, owner, page_id)
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

    target = _find_composite(session, storage, page_id, bg.id, pose.id, placement)
    written: str | None = None  # 這次新寫的合成圖（列還是暫存的 RUNNING）
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
        _, written = _store_outside_tx(session, storage, target, buf.getvalue())
        elapsed_s = time.monotonic() - t0
    target_id = target.id
    try:
        # 新合成圖標成成功與選用它在同一個交易：選不到（輸入被改）就整個回滾、刪掉暫存列與物件
        if written is not None and _finish_stored(session, target_id, written, elapsed_s=elapsed_s).rowcount == 0:
            raise HTTPException(409, INPUTS_CHANGED)
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
        if written is not None:
            _abandon(session, storage, target_id, written)
        raise
    session.refresh(page)
    return page_out(session, page)
