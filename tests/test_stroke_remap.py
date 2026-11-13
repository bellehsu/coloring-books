"""C1：改紙張／列印方式時，修線筆畫從舊的 art box 換算到新的 art box（同一個來源圖像素）。"""

import json

import numpy as np
import pytest
from sqlmodel import select

from app.models import LineartEdit, Page, PageHistory, StrokeLayer
from app.services import lineart_post
from app.services.layout import PaperSize, PrintMode, compute_layout
from app.services.lineart_render import Stroke
from app.services.page_lineart import save_strokes, strokes_from_json, strokes_to_json
from app.services.stroke_remap import art_box, remap_point, remap_strokes

SRC = (832, 1216)  # A4 家用建的書：背景的生成尺寸
A4_HOME = compute_layout(PaperSize.A4, PrintMode.HOME).content_px  # 2244×3272
A4_SHOP = compute_layout(PaperSize.A4, PrintMode.PRINT_SHOP).content_px  # 2551×3579
A5_HOME = compute_layout(PaperSize.A5, PrintMode.HOME).content_px  # 1512×2244


def _contain(img: np.ndarray, content_px) -> np.ndarray:
    """與產品線稿同一個縮放（lineart_post.resize_to_target，fit=contain）。"""
    p = lineart_post.LineartParams(target_width=content_px[0], target_height=content_px[1], fit="contain",
                                   interpolation="lanczos")
    return lineart_post.resize_to_target(img, p)


def _dot_center(content_px, sx, sy) -> tuple[float, float]:
    """來源圖 (sx, sy) 一個 5×5 黑點，縮放到 content_px 後的位置（墨水的重心，像素）。"""
    img = np.full((SRC[1], SRC[0]), 255, np.uint8)
    img[sy - 2:sy + 3, sx - 2:sx + 3] = 0
    ink = 255.0 - _contain(img, content_px).astype(float)
    # 只看黑點附近（cv2 的 Lanczos 偶爾在遠處留下 1–2 的雜訊值）
    cy, cx = np.unravel_index(np.argmax(ink), ink.shape)
    win = ink[max(0, cy - 20):cy + 21, max(0, cx - 20):cx + 21]
    ys, xs = np.nonzero(win > 0)
    wts = win[ys, xs]
    ox, oy = max(0, cx - 20), max(0, cy - 20)
    return float(ox + (xs * wts).sum() / wts.sum()), float(oy + (ys * wts).sum() / wts.sum())


def test_art_box_matches_resize_to_target():
    for content in (A4_HOME, A4_SHOP, A5_HOME):
        x0, y0, nw, nh = art_box(SRC, content)
        out = _contain(np.zeros((SRC[1], SRC[0]), np.uint8), content)  # 黑圖：art box 外是白邊
        ys, xs = np.nonzero(out < 128)
        assert (xs.min(), ys.min(), xs.max() - xs.min() + 1, ys.max() - ys.min() + 1) == (x0, y0, nw, nh)


@pytest.mark.parametrize("new", [A4_SHOP, A5_HOME], ids=["a4_print_shop", "a5_home"])
@pytest.mark.parametrize("src_pt", [(30, 600), (800, 80), (416, 1180), (200, 300)])
def test_converted_point_lands_on_the_same_source_pixel(new, src_pt):
    # 使用者在 A4 家用剛好畫在線上（那個點在線稿上的像素）
    ox, oy = _dot_center(A4_HOME, *src_pt)
    x, y = ox / (A4_HOME[0] - 1), oy / (A4_HOME[1] - 1)
    nx, ny = remap_point((x, y), SRC, A4_HOME, new)
    # apply_strokes 的像素：round(x * (w - 1))
    px, py = round(nx * (new[0] - 1)), round(ny * (new[1] - 1))
    ex, ey = _dot_center(new, *src_pt)
    assert abs(px - ex) <= 1 and abs(py - ey) <= 1, ((px, py), (ex, ey))


def test_round_trip_returns_original_coords():
    # A4 家用的 art box 是 x 2–2240（左右各 2 px 白邊）：取 art box 裡的點
    pts = [(0.002, 0.0), (0.1, 0.2), (0.5, 0.5), (0.93, 0.81), (0.998, 1.0)]
    for x, y in pts:
        a = remap_point((x, y), SRC, A4_HOME, A4_SHOP)
        b = remap_point(a, SRC, A4_SHOP, A4_HOME)
        assert b == pytest.approx((x, y), rel=1e-6, abs=1e-9)
        c = remap_point(remap_point((x, y), SRC, A4_HOME, A5_HOME), SRC, A5_HOME, A4_HOME)
        assert c == pytest.approx((x, y), rel=1e-6, abs=1e-9)


def test_points_outside_the_new_art_box_are_clamped():
    assert art_box(SRC, A4_HOME) == (2, 0, 2239, 3272)
    assert art_box(SRC, A4_SHOP) == (51, 0, 2449, 3579)
    # 印刷廠左右各 51 px 白邊：白邊裡的點換到家用，夾在 art box 的左／右緣（像素邊：x0 − 0.5、x0 + nw − 0.5）
    nx, ny = remap_point((0.0, 0.5), SRC, A4_SHOP, A4_HOME)
    assert nx * (A4_HOME[0] - 1) == pytest.approx(1.5)
    nx, _ = remap_point((1.0, 0.5), SRC, A4_SHOP, A4_HOME)
    assert nx * (A4_HOME[0] - 1) == pytest.approx(2 + 2239 - 0.5)
    # 家用左緣的白邊（x < 2）換到印刷廠：夾在 x0＝51 的邊上
    nx, _ = remap_point((0.0, 0.5), SRC, A4_HOME, A4_SHOP)
    assert nx * (A4_SHOP[0] - 1) == pytest.approx(50.5)
    # 結果一律在 0–1（API 的 UnitFloat）：A5 家用 art box 從 x＝0 開始，夾到左緣的 −0.5 px 收回 0
    assert art_box(SRC, A5_HOME)[0] == 0
    for pt in [(0.0, 0.0), (1.0, 1.0), (0.0, 1.0), (1.0, 0.0)]:
        for a, b in [(A4_SHOP, A5_HOME), (A4_HOME, A5_HOME), (A5_HOME, A4_SHOP), (A4_SHOP, A4_HOME)]:
            assert all(0.0 <= v <= 1.0 for v in remap_point(pt, SRC, a, b))


def test_remap_strokes_keeps_op_and_width():
    s = [Stroke("draw", 11.0, ((0.2, 0.5), (0.8, 0.5))), Stroke("erase", 14.0, ((0.4, 0.4),))]
    out = remap_strokes(s, SRC, A4_HOME, A4_SHOP)
    assert [(o.op, o.width_px, len(o.points)) for o in out] == [("draw", 11.0, 2), ("erase", 14.0, 1)]
    assert out[0].points[0] == remap_point((0.2, 0.5), SRC, A4_HOME, A4_SHOP)
    assert remap_strokes(s, SRC, A4_HOME, A4_HOME) == s


# ---- 改書的紙張／列印方式（PATCH /books/{id}）------------------------------------------------

from tests.test_api_lineart import app, client, selected_page  # noqa: E402,F401  (fixtures)
from tests.test_api_pages import new_page  # noqa: E402

USER = [{"op": "draw", "width_px": 11.0, "points": [[0.21, 0.37], [0.64, 0.52]]}]
AUTO = [{"op": "draw", "width_px": 11.0, "points": [[0.3, 0.1], [0.3, 0.12]]}]
PENDING = [{"op": "erase", "width_px": 14.0, "points": [[0.5, 0.5]]}]


def _strokes(d) -> list[Stroke]:
    return strokes_from_json(json.dumps(d))


def _seed(app, page_id, gen_id):
    with app.state.sessions() as s:
        save_strokes(s, page_id, gen_id, _strokes(USER), layer=StrokeLayer.USER)
        save_strokes(s, page_id, gen_id, _strokes(AUTO), layer=StrokeLayer.AUTO)
        page = s.get(Page, page_id)
        page.pending_user_strokes = strokes_to_json(_strokes(PENDING))
        s.add(page)
        s.add(PageHistory(page_id=page_id, seq=1, snapshot_json=json.dumps(
            {"background_generation_id": None, "pose_generation_id": None, "user_strokes": USER})))
        s.commit()


def _stored(app, page_id, gen_id):
    with app.state.sessions() as s:
        layers = {StrokeLayer(e.layer): strokes_from_json(e.strokes_json)
                  for e in s.exec(select(LineartEdit).where(LineartEdit.generation_id == gen_id))}
        page = s.get(Page, page_id)
        snap = json.loads(s.exec(select(PageHistory.snapshot_json).where(PageHistory.page_id == page_id)).first())
        return {
            "user": layers.get(StrokeLayer.USER),
            "auto": layers.get(StrokeLayer.AUTO),
            "pending": strokes_from_json(page.pending_user_strokes) if page.pending_user_strokes else None,
            "history": _strokes(snap["user_strokes"]),
        }


def _approx(a: list[Stroke], b: list[Stroke]):
    assert [(s.op, s.width_px) for s in a] == [(s.op, s.width_px) for s in b]
    for sa, sb in zip(a, b):
        assert list(sa.points) == [pytest.approx(p, rel=1e-6, abs=1e-9) for p in sb.points]


def test_patch_book_converts_every_stored_stroke(client, app):
    b, p, (gid,) = selected_page(client, app, book_kw={"paper_size": "a4", "print_mode": "home"})
    _seed(app, p["id"], gid)
    blank = new_page(client, b["id"])  # 沒有筆畫的頁
    ob, op_, (ogid,) = selected_page(client, app, book_kw={"paper_size": "a4", "print_mode": "home"})
    _seed(app, op_["id"], ogid)  # 另一本書
    with app.state.sessions() as s:
        blank_before = s.get(Page, blank["id"]).model_dump()
    other_before = _stored(app, op_["id"], ogid)

    r = client.patch(f"/api/books/{b['id']}", json={"print_mode": "print_shop"})
    assert r.status_code == 200, r.text
    got = _stored(app, p["id"], gid)
    want = lambda d: remap_strokes(_strokes(d), SRC, A4_HOME, A4_SHOP)  # noqa: E731
    _approx(got["user"], want(USER))
    _approx(got["auto"], want(AUTO))
    _approx(got["pending"], want(PENDING))
    _approx(got["history"], want(USER))
    assert got["user"] != _strokes(USER)  # 真的換算了

    # 別的頁、別的書不動
    with app.state.sessions() as s:
        after = s.get(Page, blank["id"]).model_dump()
        assert {k: v for k, v in after.items() if k != "updated_at"} == \
            {k: v for k, v in blank_before.items() if k != "updated_at"}
    assert _stored(app, op_["id"], ogid) == other_before

    # 改回家用：回到原本的座標
    assert client.patch(f"/api/books/{b['id']}", json={"print_mode": "home"}).status_code == 200
    back = _stored(app, p["id"], gid)
    _approx(back["user"], _strokes(USER))
    _approx(back["auto"], _strokes(AUTO))
    _approx(back["pending"], _strokes(PENDING))
    _approx(back["history"], _strokes(USER))


def test_patch_paper_size_converts_and_title_only_does_not(client, app):
    b, p, (gid,) = selected_page(client, app, book_kw={"paper_size": "a4", "print_mode": "home"})
    _seed(app, p["id"], gid)
    assert client.patch(f"/api/books/{b['id']}", json={"title": "新名"}).status_code == 200
    assert _stored(app, p["id"], gid)["user"] == _strokes(USER)
    assert client.patch(f"/api/books/{b['id']}", json={"paper_size": "a5"}).status_code == 200
    _approx(_stored(app, p["id"], gid)["user"], remap_strokes(_strokes(USER), SRC, A4_HOME, A5_HOME))
