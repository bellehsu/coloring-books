"""頁面線稿服務：依選定圖片 + 線稿設定 + 修線圖層（auto 再 user）算出線稿與 QA，並快取。"""

from __future__ import annotations

import hashlib
import io
import json
from dataclasses import dataclass

from sqlalchemy import update
from sqlmodel import Session, col, select

from app.models import Book, Generation, LineartEdit, Page, PageStatus, StrokeLayer, utcnow
from app.services import lineart_post
from app.services.layout import compute_layout
from app.services.lineart_render import (
    RENDER_VERSION, Stroke, apply_strokes, ink_to_png, render_base,
)
from app.services import qa as qa_mod
from app.services.qa import QAReport, evaluate
from app.storage import Storage, StorageNotFound

MAX_ATTEMPTS = 3


SOURCE_MISSING = "選定的圖片檔案遺失，請重新選圖或重新生成"


class LineartUnavailable(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class LineartSourceMissing(LineartUnavailable):
    """選定圖片的物件不在 Storage（或讀不出來）：線稿無法重算。圖片端點回 404。"""

    def __init__(self, message: str = SOURCE_MISSING):
        super().__init__(message)


@dataclass
class PageLineart:
    path: str
    key: str
    qa: QAReport


def _dumps(obj) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


def strokes_to_json(strokes: list[Stroke]) -> str:
    return _dumps([
        {"op": s.op, "width_px": float(s.width_px),
         "points": [[float(x), float(y)] for x, y in s.points]}
        for s in strokes
    ])


def strokes_from_json(text: str) -> list[Stroke]:
    return [
        Stroke(d["op"], float(d["width_px"]), tuple((float(x), float(y)) for x, y in d["points"]))
        for d in json.loads(text or "[]")
    ]


def _layer_json(session: Session, gen_id: int, layer: StrokeLayer) -> str:
    edit = session.get(LineartEdit, (gen_id, layer))
    return edit.strokes_json if edit else "[]"


def combine_layers_json(auto_text: str | None, user_text: str | None) -> str:
    """渲染用的完整筆畫：auto 層在前、user 層在後（使用者的橡皮擦擦得掉補線）。
    只有一層有筆畫時原樣沿用那一層的文字，所以只有 user 層的既有頁面 lineart_key 不變。"""
    auto, user = json.loads(auto_text or "[]"), json.loads(user_text or "[]")
    if not auto:
        return user_text or "[]"
    if not user:
        return auto_text
    return _dumps(auto + user)


def combined_strokes_json(session: Session, gen_id: int) -> str:
    return combine_layers_json(_layer_json(session, gen_id, StrokeLayer.AUTO),
                               _layer_json(session, gen_id, StrokeLayer.USER))


def strokes_json_by_generation(session: Session, gen_ids) -> dict[int, str]:
    """多張圖一次查：{generation_id: 合併後的筆畫 JSON}，沒有修線的圖不在結果裡。"""
    ids = list(gen_ids)
    if not ids:
        return {}
    layers: dict[int, dict[StrokeLayer, str]] = {}
    for e in session.exec(select(LineartEdit).where(col(LineartEdit.generation_id).in_(ids))).all():
        layers.setdefault(e.generation_id, {})[StrokeLayer(e.layer)] = e.strokes_json
    return {g: combine_layers_json(d.get(StrokeLayer.AUTO), d.get(StrokeLayer.USER)) for g, d in layers.items()}


def load_strokes(session: Session, gen_id: int, *, layer: StrokeLayer) -> list[Stroke]:
    return strokes_from_json(_layer_json(session, gen_id, layer))


def save_strokes(session: Session, page_id: int, gen_id: int, strokes: list[Stroke], *, layer: StrokeLayer) -> None:
    """整份覆寫一層修線（不 commit）；另一層不動。"""
    text = strokes_to_json(strokes)
    edit = session.get(LineartEdit, (gen_id, layer))
    if edit is None:
        session.add(LineartEdit(generation_id=gen_id, layer=layer, page_id=page_id, strokes_json=text))
    else:
        edit.strokes_json = text
        edit.updated_at = utcnow()
        session.add(edit)


def commit_layer(session: Session, page_id: int, gen_id: int, strokes: list[Stroke], *, layer: StrokeLayer) -> bool:
    """整份覆寫一層修線，並在同一個交易以條件式 UPDATE 確認頁面仍選定這張圖；修線後 passed 退回 draft。
    頁面已不再選定這張圖時 rollback、回 False。兩個請求同時建立同一層丟 IntegrityError（已 rollback）。"""
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
            return False
        session.commit()
        return True
    except Exception:
        session.rollback()
        raise


def _digest(*parts) -> str:
    return hashlib.sha256(_dumps(list(parts)).encode()).hexdigest()[:16]


def lineart_key(gen: Generation, page: Page, content_px, strokes_json: str) -> str:
    return _digest(RENDER_VERSION, qa_mod.QA_VERSION, gen.id, gen.image_path, str(getattr(page.line_detail, "value", page.line_detail)),
                   page.line_width_px, list(content_px), strokes_json)


def _load(storage: Storage, gen: Generation):
    try:
        if not gen.image_path:
            raise FileNotFoundError(gen.image_path)
        return lineart_post.load_image(io.BytesIO(storage.get(gen.image_path)))
    except (StorageNotFound, ValueError, OSError) as e:
        raise LineartSourceMissing() from e


def ensure_page_lineart(session: Session, storage: Storage, page_id: int, *, rerender: bool = False) -> PageLineart:
    """確保頁面線稿與 QA 是最新的。是否已有線稿只看資料庫（lineart_key／lineart_path／qa_json），
    不呼叫 storage.exists()；讀線稿時發現物件不見了，用 rerender=True 重算（見 read_page_lineart）。"""
    for _ in range(MAX_ATTEMPTS):
        page = session.get(Page, page_id)
        if page is None:
            raise LineartUnavailable("找不到這一頁")
        session.refresh(page)
        gen_id = page.selected_generation_id
        gen = session.get(Generation, gen_id) if gen_id else None
        if gen is None:
            raise LineartUnavailable("這一頁還沒有選定圖片")
        book = session.get(Book, page.book_id)
        content_px = compute_layout(book.paper_size, book.print_mode).content_px
        sj = combined_strokes_json(session, gen.id)
        key = lineart_key(gen, page, content_px, sj)
        rel = storage.lineart(page.book_id, page.id, key)
        if not rerender and page.lineart_key == key and page.lineart_path == rel and page.qa_json:
            return PageLineart(rel, key, QAReport.from_dict(json.loads(page.qa_json)))

        color = _load(storage, gen)
        ink, _metrics = render_base(color, content_px, page.line_detail, page.line_width_px)
        strokes = strokes_from_json(sj)
        if strokes:
            ink = apply_strokes(ink, strokes)
        qa = evaluate(ink, page.line_width_px)
        storage.put(rel, ink_to_png(ink))

        old_path = page.lineart_path
        values = {"lineart_key": key, "lineart_path": rel, "qa_json": _dumps(qa.to_dict())}
        demote = page.status == PageStatus.PASSED and bool(qa.errors)
        if demote:
            values.update(status=PageStatus.DRAFT, passed_at=None)
        res = session.exec(
            update(Page)
            .where(col(Page.id) == page_id,
                   col(Page.selected_generation_id) == gen.id,
                   col(Page.status) == page.status)
            .values(**values)
        )
        session.commit()
        if res.rowcount:
            if old_path and old_path != rel:
                storage.delete(old_path)
            return PageLineart(rel, key, qa)
        # 輸了競爭：自己寫的檔案若不是 DB 目前指的那個就刪掉
        current = session.exec(select(Page.lineart_path).where(col(Page.id) == page_id)).first()
        if current != rel:
            storage.delete(rel)
    raise LineartUnavailable("線稿產生時頁面一直被更動，請稍後再試")


def ink_before_user_layer(session: Session, storage: Storage, page_id: int, gen_id: int, *, with_auto: bool = True):
    """選定圖的底稿＋auto 層（還沒套 user 層）的墨水陣列，與 ensure_page_lineart 同樣的設定。
    自動補線要依渲染順序（auto 再 user）試補線時用；with_auto=False 只回底稿（頁面流程重算整層 auto）。
    來源圖片不見丟 LineartSourceMissing。"""
    page = session.get(Page, page_id)
    gen = session.get(Generation, gen_id)
    if page is None or gen is None:
        raise LineartUnavailable("這一頁還沒有選定圖片")
    book = session.get(Book, page.book_id)
    content_px = compute_layout(book.paper_size, book.print_mode).content_px
    ink, _metrics = render_base(_load(storage, gen), content_px, page.line_detail, page.line_width_px)
    auto = load_strokes(session, gen_id, layer=StrokeLayer.AUTO) if with_auto else []
    return apply_strokes(ink, auto) if auto else ink


def read_page_lineart(session: Session, storage: Storage, page_id: int) -> tuple[PageLineart, bytes]:
    """ensure_page_lineart 再讀出線稿 PNG。資料庫說有、物件卻不見了（GC 或手動刪除）就重算一次。
    來源圖片也不見了丟 LineartSourceMissing。"""
    lin = ensure_page_lineart(session, storage, page_id)
    try:
        return lin, storage.get(lin.path)
    except StorageNotFound:
        pass
    lin = ensure_page_lineart(session, storage, page_id, rerender=True)
    try:
        return lin, storage.get(lin.path)
    except StorageNotFound as e:  # 剛寫入又不見（同時被刪）：當成暫時失敗
        raise LineartUnavailable("線稿產生時頁面一直被更動，請稍後再試") from e


def render_preview(storage: Storage, gen: Generation, content_px, detail, line_width_px) -> bytes:
    """沒有修改圖層的預覽 PNG，快取到 Storage.preview（物件不在就重算）。"""
    rel, data = _preview(storage, gen, content_px, detail, line_width_px, want_bytes=True)
    return data


def ensure_preview(storage: Storage, gen: Generation, content_px, detail, line_width_px) -> str:
    """確保預覽物件存在（不在就重算並寫入），回傳它的 key；物件已在時只做 exists，不下載。"""
    return _preview(storage, gen, content_px, detail, line_width_px, want_bytes=False)[0]


def _preview(storage, gen, content_px, detail, line_width_px, *, want_bytes: bool) -> tuple[str, bytes | None]:
    d = getattr(detail, "value", detail)
    key = _digest(RENDER_VERSION, gen.id, gen.image_path, str(d), line_width_px, list(content_px))
    rel = storage.preview(gen.id, key)
    if want_bytes:
        try:
            return rel, storage.get(rel)
        except StorageNotFound:
            pass
    elif storage.exists(rel):
        return rel, None
    color = _load(storage, gen)
    ink, _ = render_base(color, tuple(content_px), detail, line_width_px)
    data = ink_to_png(ink)
    storage.put(rel, data)
    return rel, data
