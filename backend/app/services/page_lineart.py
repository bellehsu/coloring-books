"""頁面線稿服務：依選定圖片 + 線稿設定 + 修線圖層算出線稿與 QA，並快取。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from sqlalchemy import update
from sqlmodel import Session, col, select

from app.models import Book, Generation, LineartEdit, Page, PageStatus, utcnow
from app.services import lineart_post
from app.services.layout import compute_layout
from app.services.lineart_render import (
    RENDER_VERSION, Stroke, apply_strokes, ink_to_png, render_base,
)
from app.services import qa as qa_mod
from app.services.qa import QAReport, evaluate
from app.storage import Storage

MAX_ATTEMPTS = 3


class LineartUnavailable(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


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


def _strokes_json(session: Session, gen_id: int) -> str:
    edit = session.get(LineartEdit, gen_id)
    return edit.strokes_json if edit else "[]"


def load_strokes(session: Session, gen_id: int) -> list[Stroke]:
    return strokes_from_json(_strokes_json(session, gen_id))


def save_strokes(session: Session, page_id: int, gen_id: int, strokes: list[Stroke]) -> None:
    """寫入修線圖層（不 commit）。"""
    text = strokes_to_json(strokes)
    edit = session.get(LineartEdit, gen_id)
    if edit is None:
        session.add(LineartEdit(generation_id=gen_id, page_id=page_id, strokes_json=text))
    else:
        edit.strokes_json = text
        edit.updated_at = utcnow()
        session.add(edit)


def _digest(*parts) -> str:
    return hashlib.sha256(_dumps(list(parts)).encode()).hexdigest()[:16]


def lineart_key(gen: Generation, page: Page, content_px, strokes_json: str) -> str:
    return _digest(RENDER_VERSION, qa_mod.QA_VERSION, gen.id, gen.image_path, str(getattr(page.line_detail, "value", page.line_detail)),
                   page.line_width_px, list(content_px), strokes_json)


def _load(storage: Storage, gen: Generation):
    try:
        if not gen.image_path:
            raise FileNotFoundError(gen.image_path)
        return lineart_post.load_image(storage.abs(gen.image_path))
    except (FileNotFoundError, ValueError, OSError) as e:
        raise LineartUnavailable("選定的圖片檔案遺失，請重新選圖或重新生成") from e


def ensure_page_lineart(session: Session, storage: Storage, page_id: int) -> PageLineart:
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
        sj = _strokes_json(session, gen.id)
        key = lineart_key(gen, page, content_px, sj)
        rel = storage.lineart(page.book_id, page.id, key)
        if (page.lineart_key == key and page.lineart_path == rel and page.qa_json
                and storage.abs(rel).exists()):
            return PageLineart(rel, key, QAReport.from_dict(json.loads(page.qa_json)))

        color = _load(storage, gen)
        ink, _metrics = render_base(color, content_px, page.line_detail, page.line_width_px)
        strokes = strokes_from_json(sj)
        if strokes:
            ink = apply_strokes(ink, strokes)
        qa = evaluate(ink, page.line_width_px)
        storage.write(rel, ink_to_png(ink))

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


def render_preview(storage: Storage, gen: Generation, content_px, detail, line_width_px) -> str:
    """沒有修改圖層的預覽，快取到 Storage.preview。"""
    d = getattr(detail, "value", detail)
    key = _digest(RENDER_VERSION, gen.id, gen.image_path, str(d), line_width_px, list(content_px))
    rel = storage.preview(gen.id, key)
    if storage.abs(rel).exists():
        return rel
    color = _load(storage, gen)
    ink, _ = render_base(color, tuple(content_px), detail, line_width_px)
    storage.write(rel, ink_to_png(ink))
    return rel
