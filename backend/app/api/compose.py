"""頁面合成流程：背景與角色動作的生成／選用、位置草稿、確認合成。

合成圖寫成一筆 `composite` generation 並設為頁面的 selected_generation_id，
所以線稿、修線圖層（以 generation 為 key）、QA、匯出都沿用既有流程。

C 處理一頁（Phase 13）呼叫同樣的端點並帶 `?oneclick=true`：改動前先存復原快照（services/history），
會重新合成的改動清掉使用者筆畫（快照裡留著）並排頁面流程；「換一張／AI 再畫一張」的新圖好了一定換上去。
不帶 oneclick 的是舊編輯器（進階編輯），行為不變。
"""

import io
import json
import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile
from sqlalchemy import case
from sqlmodel import Session, col, select, update

from app.api.deps import (
    current_owner,
    get_session,
    get_storage,
    get_worker,
    owned_book,
    owned_generation,
    owned_page,
)
from app.api.pages import page_out
from app.api.schemas import (
    BackgroundCreate,
    BatchMaterialsIn,
    BatchMaterialsOut,
    BatchSkip,
    GenerationOut,
    PageOut,
    PagePlacement,
    PoseCreate,
    PoseRedrawIn,
    PoseRedrawOut,
    SelectGeneration,
)
from app.models import Generation, GenKind, GenStatus, Owner, Page, utcnow
from app.services import history
from app.services.compose import cutout, page_placement
from app.services.frame_fit import DEFAULT_SEED, find_interior, fit_portrait
from app.services.gen_request import encode_request
from app.services.jobs import (
    BATCH_LOCK,
    BookState,
    Job,
    background_job,
    build_job,
    enqueue_jobs,
    has_character,
    redraw_jobs,
    take,
)
from app.services.layout import compute_layout
from app.services.page_compose import (
    NEED_BACKGROUND,
    NEED_POSE,
    ComposeError,
    _compose_error,
    _load_images,
    compose_page,
    is_usable,
    slot_change_values,
    store_outside_tx,
    usable_conditions,
)
from app.services.page_state import ATTENTION_REVIEW_POSE
from app.services.pipeline import enqueue_page_task
from app.services.slots import UPLOAD_ENGINE
from app.services.uploads import cover_crop, mark_stored, read_upload
from app.services.worker import GenerationWorker
from app.storage import Storage

router = APIRouter(prefix="/api", tags=["compose"])
log = logging.getLogger(__name__)

_LABEL = {GenKind.BACKGROUND: "背景", GenKind.POSE: "角色動作"}
# C 處理一頁的請求帶 ?oneclick=true（見模組說明）
OneClick = Query(False, description="C 處理一頁：先存復原快照、排頁面流程")
_PAGE_FIELD = {GenKind.BACKGROUND: "background_generation_id", GenKind.POSE: "pose_generation_id"}


@router.post("/pages/{page_id}/backgrounds", status_code=202, response_model=GenerationOut)
def create_background(
    page_id: int,
    body: BackgroundCreate | None = None,
    oneclick: bool = OneClick,
    session: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
    worker: GenerationWorker = Depends(get_worker),
    owner: Owner = Depends(current_owner),
) -> GenerationOut:
    """產生一張背景：只用場景描述，不使用角色參考圖與草圖。body 可省略。
    oneclick（C 的「AI 再畫一張」）：好了一定換上去（上傳的背景也換回 AI 畫的）。"""
    page = owned_page(session, owner, page_id)
    book = owned_book(session, owner, page.book_id)
    gens = enqueue_jobs(session, worker, owner, [take(background_job(session, book, page), oneclick)])
    return GenerationOut.for_worker(gens[0], worker)


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
    BATCH_LOCK 讓同時多個呼叫不會重複排隊。整批一次預留 N 次當日額度：不夠就 429，一筆都不排。"""
    upload_pages = set(body.upload_page_ids) if body else set()
    with BATCH_LOCK:
        book = owned_book(session, owner, book_id)
        state = BookState(session, book_id)
        jobs: list[Job] = []
        skipped: list[BatchSkip] = []
        for page, kind, reason in state.plan(has_character(session, book), upload_pages):
            if reason is not None:
                skipped.append(
                    BatchSkip(kind=kind.value, page_id=page.id, position=page.position, reason=reason)
                )
            else:
                jobs.append(build_job(session, book, page, kind))
        gens = enqueue_jobs(session, worker, owner, jobs)  # 整批一次預留，不夠就整批 429
        queued = GenerationOut.for_worker_many(list(gens), worker)
        return BatchMaterialsOut(queued=queued, skipped=skipped)


@router.post("/pages/{page_id}/poses", status_code=202, response_model=GenerationOut)
def create_pose(
    page_id: int,
    body: PoseCreate,
    oneclick: bool = OneClick,
    session: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
    worker: GenerationWorker = Depends(get_worker),
    owner: Owner = Depends(current_owner),
) -> GenerationOut:
    """產生一個角色動作（白底、自動去背），尺寸固定 POSE_SIZE，不帶場景描述：以角色參考圖編輯出
    新動作，prompt 只有動作。不先檢查 preflight，缺東西時由生成失敗回報。
    oneclick（C 的「換一張」）：好了一定換上去，連使用者之前選的也換掉。
    body.count（C 的「一次畫 N 張」）：多的幾張是候選（不自動選用）；整批一次預留額度。回傳第 1 張。"""
    page = owned_page(session, owner, page_id)
    book = owned_book(session, owner, page.book_id)
    if not has_character(session, book):
        raise HTTPException(409, "請先設定角色")
    jobs = redraw_jobs(session, book, page, body.action, body.count, take_first=oneclick, review=False)
    gens = enqueue_jobs(session, worker, owner, jobs)
    return GenerationOut.for_worker(gens[0], worker)


@router.post("/books/{book_id}/poses/redraw", status_code=202, response_model=PoseRedrawOut)
def redraw_poses(
    book_id: int,
    body: PoseRedrawIn,
    session: Session = Depends(get_session),
    worker: GenerationWorker = Depends(get_worker),
    owner: Owner = Depends(current_owner),
) -> PoseRedrawOut:
    """B 的「重畫 N 頁的主角」：每頁用目前的動作描述畫 body.count 張。第 1 張好了自動換上去（有復原快照）
    並標成 review_pose（請看一下：換了新的主角動作），其餘是候選。沒有動作描述的頁略過。
    page_ids 去掉重複（保留順序），都要是這本書的頁。整批一次預留額度：不夠就 429，一筆都不排。"""
    with BATCH_LOCK:
        book = owned_book(session, owner, book_id)
        if not has_character(session, book):
            raise HTTPException(409, "請先設定角色")
        ids = list(dict.fromkeys(body.page_ids))
        pages = {p.id: p for p in session.exec(
            select(Page).where(col(Page.book_id) == book_id, col(Page.id).in_(ids))
        ).all()}
        if len(pages) != len(ids):
            raise HTTPException(400, "page_ids 必須是這本繪本的頁面")
        jobs: list[Job] = []
        skipped: list[BatchSkip] = []
        for pid in ids:
            page = pages[pid]
            if not page.action_prompt.strip():
                skipped.append(BatchSkip(kind="pose", page_id=page.id, position=page.position, reason="沒有動作描述"))
                continue
            jobs.extend(redraw_jobs(session, book, page, page.action_prompt, body.count,
                                    take_first=True, review=True))
        gens = enqueue_jobs(session, worker, owner, jobs)  # 整批一次預留，不夠就整批 429
        return PoseRedrawOut(queued=GenerationOut.for_worker_many(list(gens), worker), skipped=skipped)


def _pick(session: Session, owner: Owner, page_id: int, gen_id: int, kind: GenKind,
          oneclick: bool = False, worker: GenerationWorker | None = None) -> PageOut:
    """選用背景／角色動作。oneclick：先存快照、筆畫清掉（重新合成）、排頁面流程；選的就是目前那張則什麼都不做。
    C 選另一個角色動作（oneclick）也清掉軟標記 review_pose（使用者看過、自己選了）；換背景不動它。"""
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
    if oneclick:
        session.refresh(page)
        if getattr(page, field) == gen.id:
            return page_out(session, page)
        history.push_snapshot(session, page)
        history.set_pending_strokes(session, page_id, history.CLEARED)
    still_valid = (
        select(Generation.id)
        .where(col(Generation.id) == gen.id, col(Generation.page_id) == page_id,
               *usable_conditions(kind))
        .exists()
    )
    values = slot_change_values(field, gen.id)
    if oneclick and kind == GenKind.POSE:
        review = col(Page.attention) == ATTENTION_REVIEW_POSE
        values["attention"] = case((review, None), else_=col(Page.attention))
        values["attention_detail"] = case((review, None), else_=col(Page.attention_detail))
    res = session.exec(
        update(Page)
        .where(col(Page.id) == page_id, still_valid)
        .values(**values)
    )
    if res.rowcount == 0:
        session.rollback()
        raise HTTPException(409, f"這張{label}已無法選用（可能剛被捨棄）")
    if oneclick:
        enqueue_page_task(session, page_id)
    session.commit()
    if oneclick and worker is not None:
        worker.notify()
    session.refresh(page)
    return page_out(session, page)


@router.post("/pages/{page_id}/background", response_model=PageOut)
def select_background(
    page_id: int,
    body: SelectGeneration,
    oneclick: bool = OneClick,
    session: Session = Depends(get_session),
    worker: GenerationWorker = Depends(get_worker),
    owner: Owner = Depends(current_owner),
) -> PageOut:
    return _pick(session, owner, page_id, body.generation_id, GenKind.BACKGROUND, oneclick, worker)


@router.post("/pages/{page_id}/backgrounds/upload", status_code=201, response_model=PageOut)
def upload_background(
    page_id: int,
    file: UploadFile,
    oneclick: bool = OneClick,
    session: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
    worker: GenerationWorker = Depends(get_worker),
    owner: Owner = Depends(current_owner),
) -> PageOut:
    """上傳圖片直接當這頁的背景：置中裁切成書的生成尺寸，存成 engine="upload" 的背景並選用。"""
    page = owned_page(session, owner, page_id)
    book = owned_book(session, owner, page.book_id)
    started = time.monotonic()
    width, height = compute_layout(book.paper_size, book.print_mode).generation_size
    src, original_size = read_upload(file, (width, height))
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
        engine=UPLOAD_ENGINE,
        workflow_name=None,
        models_json="[]",
        params_json=json.dumps(params, ensure_ascii=False, sort_keys=True),
        started_at=utcnow(),
        elapsed_s=elapsed_s,
    )
    gen_id, rel = store_outside_tx(session, storage, gen, buf.getvalue())
    mark_stored(session, storage, gen, rel, "上傳的背景剛被移除，請重新上傳")
    # 背景已經是一筆有圖的成功紀錄：之後選用失敗（例如剛被捨棄）也不刪它的物件
    return _pick(session, owner, page_id, gen_id, GenKind.BACKGROUND, oneclick, worker)


@router.post("/pages/{page_id}/pose", response_model=PageOut)
def select_pose(
    page_id: int,
    body: SelectGeneration,
    oneclick: bool = OneClick,
    session: Session = Depends(get_session),
    worker: GenerationWorker = Depends(get_worker),
    owner: Owner = Depends(current_owner),
) -> PageOut:
    return _pick(session, owner, page_id, body.generation_id, GenKind.POSE, oneclick, worker)


@router.put("/pages/{page_id}/placement", response_model=PageOut)
def save_placement(
    page_id: int,
    body: PagePlacement,
    oneclick: bool = OneClick,
    session: Session = Depends(get_session),
    worker: GenerationWorker = Depends(get_worker),
    owner: Owner = Depends(current_owner),
) -> PageOut:
    """位置草稿（拖拉時自動存）；不產生合成圖、不改頁面狀態。
    oneclick（C：放開拖拉時存一次）：位置變了就先存快照、筆畫清掉，排頁面流程重新合成。"""
    page = owned_page(session, owner, page_id)
    placement = body.to_placement().to_dict()
    if oneclick:
        session.refresh(page)
        if page_placement(page.placement_json).key() == body.to_placement().key():
            return page_out(session, page)  # 位置沒變：不重新合成、不存快照
        try:
            history.push_snapshot(session, page)
            session.exec(update(Page).where(col(Page.id) == page_id).values(
                placement_json=json.dumps(placement), pending_user_strokes=history.CLEARED, updated_at=utcnow()))
            enqueue_page_task(session, page_id)
            session.commit()
        except Exception:
            session.rollback()
            raise
        worker.notify()
        session.refresh(page)
        return page_out(session, page)
    page.placement_json = json.dumps(placement)
    page.updated_at = utcnow()
    session.add(page)
    session.commit()
    return page_out(session, page)


NO_FRAME = "這張背景找不到框，照一般擺放"


@router.post("/pages/{page_id}/frame-fit", response_model=PagePlacement)
def frame_fit(
    page_id: int,
    session: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
    owner: Owner = Depends(current_owner),
) -> PagePlacement:
    """舞台的「放進框裡」：用頁面選定的背景與角色動作，在預設種子點找框並算出肖像位置（保留目前的 flip）。
    什麼都不存；背景上找不到框回 422。"""
    page = owned_page(session, owner, page_id)
    bg = session.get(Generation, page.background_generation_id) if page.background_generation_id else None
    if not is_usable(bg, GenKind.BACKGROUND, page_id):
        raise HTTPException(409, NEED_BACKGROUND)
    pose = session.get(Generation, page.pose_generation_id) if page.pose_generation_id else None
    if not is_usable(pose, GenKind.POSE, page_id):
        raise HTTPException(409, NEED_POSE)
    try:
        bg_img, pose_img, mask_img = _load_images(storage, bg, pose)
        size = cutout(pose_img, mask_img).size
    except ComposeError as e:
        raise HTTPException(409, e.message) from e
    except ValueError as e:
        raise HTTPException(409, _compose_error(e).message) from e
    interior = find_interior(bg_img, DEFAULT_SEED)
    if interior is None:
        raise HTTPException(422, NO_FRAME)
    cx, cy, height = fit_portrait(interior, size)
    return PagePlacement(cx=cx, cy=cy, height=height, flip=page_placement(page.placement_json).flip,
                         frame={"x": DEFAULT_SEED[0], "y": DEFAULT_SEED[1]}, autofit=True)


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
    try:
        compose_page(session, storage, page_id)
    except ComposeError as e:
        raise HTTPException(409, e.message) from e
    # 舊編輯器的確認合成優先：C 等著寫回新合成圖的筆畫作廢（否則流程會蓋掉這裡接著畫的線）
    session.exec(update(Page).where(col(Page.id) == page_id, col(Page.pending_user_strokes).is_not(None))
                 .values(pending_user_strokes=None))
    session.commit()
    session.refresh(page)
    return page_out(session, page)
