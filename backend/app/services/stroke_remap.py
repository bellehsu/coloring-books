"""修線筆畫的座標換算（review C1）：改紙張／列印方式時，把筆畫從舊的 art box 換到新的 art box。

筆畫存成相對整個內容框（content_px）的 0–1 座標（apply_strokes：像素 = round(x * (w - 1))），但線稿是把來源圖
（頁面選定圖，generation 的 width×height）以 fit=contain 放進內容框（lineart_post.contain_box）。內容框的長寬比
一變，白邊跟著變，同一個相對座標就對不到原本的線。所以書的 paper_size／print_mode 改變時，同一個交易把
這本書每一處存著的筆畫換算成「新內容框裡、同一個來源圖位置」的座標（remap_book_strokes）：

- LineartEdit 的 user 與 auto 層（每張圖一列，用那張圖的尺寸；頁面流程之後還會重算 auto 層）；
- Page.pending_user_strokes（等重新合成寫回的 user 層：新合成圖是背景的尺寸，沒有背景就用選定圖）；
- page_history 快照裡的 user_strokes（用快照的背景；沒有就用頁面目前的背景或選定圖）。

落在新 art box 外的點夾到 art box 邊上。筆寬（像素）不變：線稿的線寬也是固定像素。
"""

from __future__ import annotations

import json
from collections.abc import Sequence

from sqlmodel import Session, col, select

from app.models import Generation, LineartEdit, Page, PageHistory, utcnow
from app.services.lineart_post import contain_box
from app.services.lineart_render import Stroke
from app.services.page_lineart import strokes_from_json, strokes_to_json

Size = tuple[int, int]


def art_box(src_wh: Size, content_px: Size) -> tuple[int, int, int, int]:
    """來源圖在內容框裡的位置 (x0, y0, nw, nh)：與線稿縮放（resize_to_target, fit=contain）同一個公式。"""
    return contain_box(src_wh, content_px)


def _axis(v: float, t_old: int, o_old: int, n_old: int, t_new: int, o_new: int, n_new: int) -> float:
    # 內容框像素 → art box 裡的比例 u（0–1 是 art box 的左右邊；像素中心與 cv2.resize 的取樣對齊）
    # → 新內容框像素；art box 外（u 不在 0–1）夾到新 art box 的邊上。結果仍在 0–1（API 的 UnitFloat）
    u = min(max((v * (t_old - 1) - o_old + 0.5) / n_old, 0.0), 1.0)
    p = o_new + u * n_new - 0.5
    return min(max(p / (t_new - 1), 0.0), 1.0)


def remap_point(pt: tuple[float, float], src_wh: Size, old_content: Size, new_content: Size) -> tuple[float, float]:
    """一個 0–1 座標：舊內容框 → 新內容框，對到同一個來源圖位置。"""
    ox, oy, ow, oh = art_box(src_wh, old_content)
    nx, ny, nw, nh = art_box(src_wh, new_content)
    return (_axis(pt[0], old_content[0], ox, ow, new_content[0], nx, nw),
            _axis(pt[1], old_content[1], oy, oh, new_content[1], ny, nh))


def remap_strokes(strokes: Sequence[Stroke], src_wh: Size, old_content: Size, new_content: Size) -> list[Stroke]:
    """整份筆畫換算（op、筆寬不變）；內容框沒變時原樣回傳。"""
    if tuple(old_content) == tuple(new_content):
        return list(strokes)
    return [Stroke(s.op, s.width_px, tuple(remap_point(p, src_wh, old_content, new_content) for p in s.points))
            for s in strokes]


def _remap_json(text: str, src_wh: Size, old_content: Size, new_content: Size) -> str:
    return strokes_to_json(remap_strokes(strokes_from_json(text), src_wh, old_content, new_content))


def remap_book_strokes(session: Session, book_id: int, old_content: Size, new_content: Size) -> int:
    """把這本書所有存著的筆畫從 old_content 換到 new_content（不 commit；呼叫端和改書同一個交易）。
    沒有筆畫的列不動。回傳改了幾列（LineartEdit＋頁面＋快照）。"""
    if tuple(old_content) == tuple(new_content):
        return 0
    pages = {p.id: p for p in session.exec(select(Page).where(col(Page.book_id) == book_id)).all()}
    if not pages:
        return 0
    sizes: dict[int, Size | None] = {}

    def size_of(*gen_ids: int | None) -> Size | None:
        for gid in gen_ids:
            if gid is None:
                continue
            if gid not in sizes:
                g = session.get(Generation, gid)
                sizes[gid] = (g.width, g.height) if g is not None and g.width and g.height else None
            if sizes[gid] is not None:
                return sizes[gid]
        return None

    changed = 0
    edits = session.exec(select(LineartEdit).where(col(LineartEdit.page_id).in_(list(pages)))).all()
    for e in edits:
        wh = size_of(e.generation_id)
        if wh is None or not strokes_from_json(e.strokes_json):
            continue
        e.strokes_json = _remap_json(e.strokes_json, wh, old_content, new_content)
        e.updated_at = utcnow()
        session.add(e)
        changed += 1

    for p in pages.values():
        if not p.pending_user_strokes or not strokes_from_json(p.pending_user_strokes):
            continue
        wh = size_of(p.background_generation_id, p.selected_generation_id)
        if wh is None:
            continue
        p.pending_user_strokes = _remap_json(p.pending_user_strokes, wh, old_content, new_content)
        session.add(p)
        changed += 1

    rows = session.exec(select(PageHistory).where(col(PageHistory.page_id).in_(list(pages)))).all()
    for h in rows:
        snap = json.loads(h.snapshot_json)
        user = snap.get("user_strokes") or []
        if not user:
            continue
        p = pages[h.page_id]
        wh = size_of(snap.get("background_generation_id"), p.background_generation_id, p.selected_generation_id)
        if wh is None:
            continue
        snap["user_strokes"] = json.loads(_remap_json(json.dumps(user), wh, old_content, new_content))
        h.snapshot_json = json.dumps(snap, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        session.add(h)
        changed += 1
    return changed
