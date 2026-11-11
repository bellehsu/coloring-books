"""線稿、修改圖層、QA 與預覽端點（頁面編輯器用）。

所有端點都是同步 `def`：線稿渲染與 QA 是 CPU 工作，讓 FastAPI 放進 threadpool。
"""

import io

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from PIL import Image, ImageDraw
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, update

from app.api.deps import current_owner, get_session, get_storage, owned_book, owned_generation, owned_page
from app.api.files import signed_redirect
from app.api.schemas import (
    MAX_STROKES,
    MAX_TOTAL_POINTS,
    AutofixIn,
    AutofixOut,
    EditsIn,
    EditsOut,
    QAOut,
    StrokeOut,
)
from app.models import GenStatus, Owner, Page, PageStatus, StrokeLayer, utcnow
from app.services.layout import compute_layout
from app.services.lineart_render import (
    LINE_WIDTH_DEFAULT,
    LINE_WIDTH_MAX,
    LINE_WIDTH_MIN,
    LineDetail,
    Stroke,
    apply_strokes,
)
from app.services.page_lineart import (
    LineartSourceMissing,
    LineartUnavailable,
    PageLineart,
    ensure_page_lineart,
    ensure_preview,
    load_strokes,
    read_page_lineart,
    save_strokes,
    strokes_to_json,
)
from app.services.qa import evaluate
from app.storage import Storage

router = APIRouter(prefix="/api", tags=["lineart"])

AUTOFIX_MAX_PASSES = 3  # ruling 2026-11-06：補線會讓別的近缺口變成缺口，所以重複幾輪
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
    session: Session, page_id: int, gen_id: int, strokes: list[Stroke], *, layer: StrokeLayer
) -> None:
    """一層修改圖層與「頁面仍選定這張圖」的條件式 UPDATE 在同一個交易；修線後 passed 退回 draft。"""
    try:
        save_strokes(session, page_id, gen_id, strokes, layer=layer)
        session.flush()
        res = session.exec(
            update(Page)
            .where(col(Page.id) == page_id, col(Page.selected_generation_id) == gen_id)
            .values(status=PageStatus.DRAFT, passed_at=None, updated_at=utcnow())
        )
        if res.rowcount == 0:
            session.rollback()
            raise HTTPException(409, STALE_GENERATION)
        session.commit()
    except IntegrityError as e:  # 兩個請求同時建立同一張圖的修改圖層
        session.rollback()
        raise HTTPException(409, "修改圖層剛被另一個請求更新，請重試") from e


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
    session: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
    owner: Owner = Depends(current_owner),
) -> QAOut:
    gid = _selected_or_409(owned_page(session, owner, page_id), body.generation_id)
    strokes = [
        Stroke(s.op, float(s.width_px), tuple((float(x), float(y)) for x, y in s.points))
        for s in body.strokes
    ]
    # 內容沒變（例如前端自動存檔）就不寫入、也不退回 passed
    if strokes_to_json(strokes) != strokes_to_json(load_strokes(session, gid, layer=StrokeLayer.USER)):
        _commit_strokes(session, page_id, gid, strokes, layer=StrokeLayer.USER)
    return _qa_out(session, owner, page_id, _ensure(session, storage, owner, page_id))


def _read_ink(data: bytes) -> np.ndarray:
    with Image.open(io.BytesIO(data)) as im:
        return np.asarray(im.convert("L")) < 128


def _bridge_stroke(bridge, w: int, h: int, width_px: int) -> Stroke:
    """QA 的相對座標是 x/w；apply_strokes 用 x*(w-1)，換算成同一個像素。"""
    def conv(x: float, y: float) -> tuple[float, float]:
        return (min(1.0, max(0.0, round(x * w) / (w - 1))), min(1.0, max(0.0, round(y * h) / (h - 1))))

    (ex, ey), (qx, qy) = bridge
    return Stroke("draw", float(width_px), (conv(ex, ey), conv(qx, qy)))


@router.post("/pages/{page_id}/lineart/autofix", response_model=AutofixOut)
def autofix(
    page_id: int,
    body: AutofixIn,
    session: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
    owner: Owner = Depends(current_owner),
) -> AutofixOut:
    """對目前 QA 的每個缺口補一筆 draw（加在 auto 修改圖層最後面），重算 QA，最多 AUTOFIX_MAX_PASSES 輪。

    只保留讓缺口數減少的那幾輪；某輪沒減少（或沒有缺口了）就停。全部筆畫一次存檔。
    使用者的修線（user 層）不動；筆數上限各層分開算。
    """
    gid = _selected_or_409(owned_page(session, owner, page_id), body.generation_id)
    lin, data = _ensure_png(session, storage, owner, page_id, image_route=False)
    if not lin.qa.bridges:
        return _qa_out(session, owner, page_id, lin, AutofixOut, autofix_added=0)

    page = owned_page(session, owner, page_id)
    width = page.line_width_px
    existing = load_strokes(session, gid, layer=StrokeLayer.AUTO)
    points_left = MAX_TOTAL_POINTS - sum(len(s.points) for s in existing)
    if len(existing) >= MAX_STROKES or points_left < 2:
        raise HTTPException(409, f"修改圖層已達上限（{MAX_STROKES} 筆／{MAX_TOTAL_POINTS} 點），無法自動補線")

    ink = _read_ink(data)
    h, w = ink.shape
    added: list[Stroke] = []
    gap_count, bridges = lin.qa.gap_count, lin.qa.bridges
    for _ in range(AUTOFIX_MAX_PASSES):
        room = min(MAX_STROKES - len(existing) - len(added), (points_left - 2 * len(added)) // 2)
        batch = [_bridge_stroke(b, w, h, width) for b in bridges[:max(0, room)]]
        if not batch:
            break
        trial = apply_strokes(ink, batch)
        report = evaluate(trial, width)
        if report.gap_count >= gap_count:
            break  # 這一輪沒有進展：不留它的筆畫
        ink, added = trial, added + batch
        gap_count, bridges = report.gap_count, report.bridges
        if gap_count == 0:
            break

    if not added:
        return _qa_out(session, owner, page_id, lin, AutofixOut, autofix_added=0)
    _commit_strokes(session, page_id, gid, existing + added, layer=StrokeLayer.AUTO)
    return _qa_out(session, owner, page_id, _ensure(session, storage, owner, page_id), AutofixOut,
                   autofix_added=len(added))


@router.get("/generations/{gen_id}/lineart-preview")
def lineart_preview(
    request: Request,
    gen_id: int,
    detail: LineDetail = LineDetail.STANDARD,
    line_width_px: int = Query(LINE_WIDTH_DEFAULT, ge=LINE_WIDTH_MIN, le=LINE_WIDTH_MAX),
    session: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
    owner: Owner = Depends(current_owner),
) -> Response:
    """角色或頁面候選的線稿預覽（不含修改圖層），依所屬繪本目前的內容尺寸。"""
    gen = owned_generation(session, owner, gen_id)
    if gen.status != GenStatus.SUCCEEDED or not gen.image_path:
        raise HTTPException(404, "這筆生成紀錄還沒有圖片")
    book = owned_book(session, owner, gen.book_id)
    content_px = compute_layout(book.paper_size, book.print_mode).content_px
    try:
        key = ensure_preview(storage, gen, content_px, detail, line_width_px)
    except LineartUnavailable as e:
        raise HTTPException(404, IMAGE_MISSING) from e
    return signed_redirect(request, storage, key)
