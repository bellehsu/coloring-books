"""線稿、修改圖層、QA 與預覽端點（頁面編輯器用）。

所有端點都是同步 `def`：線稿渲染與 QA 是 CPU 工作，讓 FastAPI 放進 threadpool。
"""

import io

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from PIL import Image, ImageDraw
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from app.api.deps import (
    current_owner, get_session, get_storage, get_worker, owned_book, owned_page,
)
from app.api.files import signed_redirect
from app.api.schemas import (
    AutofixIn,
    AutofixOut,
    EditsIn,
    EditsOut,
    QAOut,
    StrokeOut,
)
from app.models import Generation, Owner, Page, StrokeLayer
from app.services import history
from app.services.pipeline import ATTENTION_FAILED, enqueue_page_task
from app.services.worker import GenerationWorker
from app.services.autofix import MAX_STROKES, MAX_TOTAL_POINTS, plan_autofix
from app.services.imageio import png_to_ink
from app.services.layout import PaperSize, PrintMode, compute_layout
from app.services.lineart_render import Stroke
from app.services.page_lineart import (
    STALE_LAYOUT,
    LayoutKey,
    LineartSourceMissing,
    LineartUnavailable,
    PageLineart,
    book_layout_key,
    commit_layer,
    ensure_page_lineart,
    ink_before_user_layer,
    load_strokes,
    on_pipeline,
    read_page_lineart,
    strokes_to_json,
)
from app.storage import Storage

router = APIRouter(prefix="/api", tags=["lineart"])

OVERLAY_GAP_RADIUS_PX = 40
OVERLAY_GAP_LINE_PX = 6
OVERLAY_BOX_LINE_PX = 4
OVERLAY_GAP_COLOR = (255, 0, 0)
OVERLAY_TRIM_COLOR = (0, 0, 255)
OVERLAY_SAFE_COLOR = (0, 160, 0)

NO_SELECTION = "這一頁還沒有選定圖片"
IMAGE_MISSING = "圖片檔案遺失"
STALE_GENERATION = "這張圖已不是這一頁目前選定的圖片，請重新載入頁面"


def _ensure(session: Session, storage: Storage, owner: Owner, page_id: int) -> PageLineart:
    owned_page(session, owner, page_id)
    try:
        return ensure_page_lineart(session, storage, page_id)
    except LineartUnavailable as e:
        raise HTTPException(409, e.message) from e


def _ensure_png(
    session: Session, storage: Storage, owner: Owner, page_id: int, *, image_route: bool
) -> tuple[PageLineart, bytes]:
    """確保線稿並讀出 PNG（線稿物件不見了會重算）。選定圖片的物件遺失時：
    圖片端點回 404「圖片檔案遺失」；JSON 端點與 _ensure 一樣回 409。"""
    owned_page(session, owner, page_id)
    try:
        return read_page_lineart(session, storage, page_id)
    except LineartSourceMissing as e:
        if image_route:
            raise HTTPException(404, IMAGE_MISSING) from e
        raise HTTPException(409, e.message) from e
    except LineartUnavailable as e:
        raise HTTPException(409, e.message) from e


def _qa_out(session: Session, owner: Owner, page_id: int, lin: PageLineart, cls=QAOut, **extra) -> QAOut:
    page = owned_page(session, owner, page_id)
    session.refresh(page)  # ensure_page_lineart 可能剛把頁面退回草稿
    return cls.build(page, lin.qa, lin.key, **extra)


def _png_response(data: bytes) -> Response:
    return Response(content=data, media_type="image/png")


def _selected_or_409(page: Page, generation_id: int | None = None) -> int:
    gid = page.selected_generation_id
    if gid is None:
        raise HTTPException(409, NO_SELECTION)
    if generation_id is not None and generation_id != gid:
        raise HTTPException(409, STALE_GENERATION)
    return gid


def _commit_strokes(
    session: Session, page_id: int, gen_id: int, strokes: list[Stroke], *, layer: StrokeLayer, demote: bool = True,
    layout: LayoutKey | None = None,
) -> None:
    """一層修改圖層與「頁面仍選定這張圖（、書本版面仍是 layout）」的條件式 UPDATE 在同一個交易；
    修線後 passed 退回 draft（demote）。"""
    try:
        ok = commit_layer(session, page_id, gen_id, strokes, layer=layer, demote=demote, layout=layout)
    except IntegrityError as e:  # 兩個請求同時建立同一張圖的修改圖層
        raise HTTPException(409, "修改圖層剛被另一個請求更新，請重試") from e
    if not ok:
        page = session.get(Page, page_id)
        still_selected = page is not None and page.selected_generation_id == gen_id
        raise HTTPException(409, STALE_LAYOUT if still_selected and layout is not None else STALE_GENERATION)


@router.get("/pages/{page_id}/lineart")
def get_lineart(
    request: Request,
    page_id: int,
    session: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
    owner: Owner = Depends(current_owner),
) -> Response:
    """線稿就是一個存放的物件：確認物件在（不在就重算，來源也不見則 404）後轉到簽章網址。"""
    owned_page(session, owner, page_id)
    try:
        lin = ensure_page_lineart(session, storage, page_id)
        if not storage.exists(lin.path):  # 資料庫說有、物件卻不見了：重算一次
            lin, _data = _ensure_png(session, storage, owner, page_id, image_route=True)
    except LineartSourceMissing as e:
        raise HTTPException(404, IMAGE_MISSING) from e
    except LineartUnavailable as e:
        raise HTTPException(409, e.message) from e
    return signed_redirect(request, storage, lin.path)


@router.get("/pages/{page_id}/qa", response_model=QAOut)
def get_qa(
    page_id: int,
    session: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
    owner: Owner = Depends(current_owner),
) -> QAOut:
    return _qa_out(session, owner, page_id, _ensure(session, storage, owner, page_id))


@router.get("/pages/{page_id}/qa/overlay.png")
def get_qa_overlay(
    page_id: int,
    session: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
    owner: Owner = Depends(current_owner),
) -> Response:
    lin, data = _ensure_png(session, storage, owner, page_id, image_route=True)
    page = owned_page(session, owner, page_id)
    book = owned_book(session, owner, page.book_id)
    with Image.open(io.BytesIO(data)) as src:
        im = src.convert("RGB")
    draw = ImageDraw.Draw(im)
    w, h = im.size
    r = OVERLAY_GAP_RADIUS_PX
    for gx, gy in lin.qa.gaps:
        cx, cy = round(gx * w), round(gy * h)
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=OVERLAY_GAP_COLOR, width=OVERLAY_GAP_LINE_PX)
    lay = compute_layout(book.paper_size, book.print_mode)
    for box, color in ((lay.trim_box_px, OVERLAY_TRIM_COLOR), (lay.safe_box_px, OVERLAY_SAFE_COLOR)):
        if box is not None:
            x0, y0, x1, y1 = box
            draw.rectangle([x0, y0, x1 - 1, y1 - 1], outline=color, width=OVERLAY_BOX_LINE_PX)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return _png_response(buf.getvalue())


def _edits_out(gen_id: int, strokes: list[Stroke]) -> EditsOut:
    return EditsOut(
        generation_id=gen_id,
        strokes=[StrokeOut(op=s.op, width_px=s.width_px, points=list(s.points)) for s in strokes],
    )


@router.get("/pages/{page_id}/lineart/edits", response_model=EditsOut)
def get_edits(
    page_id: int,
    session: Session = Depends(get_session),
    owner: Owner = Depends(current_owner),
) -> EditsOut:
    gid = _selected_or_409(owned_page(session, owner, page_id))
    return _edits_out(gid, load_strokes(session, gid, layer=StrokeLayer.USER))


@router.put("/pages/{page_id}/lineart/edits", response_model=QAOut)
def put_edits(
    page_id: int,
    body: EditsIn,
    oneclick: bool = Query(False, description="C 處理一頁：先存復原快照、排頁面流程"),
    session: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
    worker: GenerationWorker = Depends(get_worker),
    owner: Owner = Depends(current_owner),
) -> QAOut:
    """使用者的筆畫（user 層）。oneclick（C 的畫筆／擦除）：先存復原快照；流程做的頁不退回草稿，
    排頁面流程重算補線（跑完仍可以印）。等著重新合成時不能畫（409）。"""
    page = owned_page(session, owner, page_id)
    gid = _selected_or_409(page, body.generation_id)
    layout = book_layout_key(session, page_id)
    if body.content_px is not None:
        current = compute_layout(PaperSize(layout[0]), PrintMode(layout[1])).content_px
        if tuple(body.content_px) != tuple(current):  # 用戶端還開著舊版面（review 2026-11-13 bug 1）
            raise HTTPException(409, STALE_LAYOUT)
    if oneclick and page.pending_user_strokes is not None:
        failed = page.attention == ATTENTION_FAILED  # 流程失敗了：沒有東西在跑，要先「再試一次」
        raise HTTPException(409, history.RECOMPOSE_FAILED if failed else history.RECOMPOSE_PENDING)
    strokes = [
        Stroke(s.op, float(s.width_px), tuple((float(x), float(y)) for x, y in s.points))
        for s in body.strokes
    ]
    # 內容沒變（例如前端自動存檔）就不寫入、也不退回 passed
    if strokes_to_json(strokes) != strokes_to_json(load_strokes(session, gid, layer=StrokeLayer.USER)):
        rerun = oneclick and on_pipeline(page, session.get(Generation, gid))
        if oneclick:
            history.push_snapshot(session, page)  # 與筆畫同一個交易（_commit_strokes 提交）
        elif page.pending_user_strokes is not None:
            history.set_pending_strokes(session, page_id, None)  # 舊編輯器的修線優先：不再寫回 C 的筆畫
        if rerun:
            enqueue_page_task(session, page_id)
        _commit_strokes(session, page_id, gid, strokes, layer=StrokeLayer.USER, demote=not rerun,
                        layout=layout if body.content_px is not None else None)
        if rerun:
            worker.notify()
    return _qa_out(session, owner, page_id, _ensure(session, storage, owner, page_id))


@router.post("/pages/{page_id}/lineart/autofix", response_model=AutofixOut)
def autofix(
    page_id: int,
    body: AutofixIn,
    oneclick: bool = Query(False, description="C 處理一頁：流程做的頁補了線也不退回草稿"),
    session: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
    owner: Owner = Depends(current_owner),
) -> AutofixOut:
    """對目前 QA 的每個缺口補一筆 draw（加在 auto 修改圖層最後面），重算 QA，最多 AUTOFIX_MAX_PASSES 輪
    （services/autofix.plan_autofix）。

    只保留讓缺口數減少的那幾輪；某輪沒減少（或沒有缺口了）就停。全部筆畫一次存檔。
    使用者的修線（user 層）不動；筆數上限各層分開算。試補線依渲染順序（auto 再 user），
    會被使用者橡皮擦擦到的補線不補。
    oneclick（C 的「自動補線」）：流程做的頁不退回草稿（之後流程重算 auto 層時仍會補上）。
    """
    gid = _selected_or_409(owned_page(session, owner, page_id), body.generation_id)
    layout = book_layout_key(session, page_id)  # 補線算好到存檔之間版面改了：不存舊座標（409）
    lin, data = _ensure_png(session, storage, owner, page_id, image_route=False)
    if not lin.qa.bridges:
        return _qa_out(session, owner, page_id, lin, AutofixOut, autofix_added=0)

    page = owned_page(session, owner, page_id)
    width = page.line_width_px
    existing = load_strokes(session, gid, layer=StrokeLayer.AUTO)
    points_left = MAX_TOTAL_POINTS - sum(len(s.points) for s in existing)
    if len(existing) >= MAX_STROKES or points_left < 2:
        raise HTTPException(409, f"修改圖層已達上限（{MAX_STROKES} 筆／{MAX_TOTAL_POINTS} 點），無法自動補線")

    # 依渲染順序試補線：底稿＋auto 層＋新補線，最後才套 user 層（使用者的橡皮擦會擦掉補線）
    user = load_strokes(session, gid, layer=StrokeLayer.USER)
    if user:
        try:
            pre = ink_before_user_layer(session, storage, page_id, gid, layout=layout)
        except LineartUnavailable as e:
            raise HTTPException(409, e.message) from e
    else:
        pre = png_to_ink(data)  # 沒有 user 層：線稿圖就是底稿＋auto 層
    added = plan_autofix(pre, user, lin.qa.gap_count, lin.qa.bridges, width,
                         room_strokes=MAX_STROKES - len(existing), room_points=points_left)

    if not added:
        return _qa_out(session, owner, page_id, lin, AutofixOut, autofix_added=0)
    keep = oneclick and on_pipeline(page, session.get(Generation, gid))
    _commit_strokes(session, page_id, gid, existing + added, layer=StrokeLayer.AUTO, demote=not keep,
                    layout=layout)
    return _qa_out(session, owner, page_id, _ensure(session, storage, owner, page_id), AutofixOut,
                   autofix_added=len(added))
