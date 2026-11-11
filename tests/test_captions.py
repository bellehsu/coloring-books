"""頁面文字（中英雙語空心字）印在線稿下方的文字帶（Phase 11 spec §4.2、§4.6）。"""

from __future__ import annotations

import io
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from skimage.morphology import skeletonize

from app.api import export as export_api
from app.config import load_settings
from app.main import create_app
from app.services import captions as captions_mod
from app.services import qa
from app.services.captions import (
    CAPTION_FONT_MISSING,
    CaptionFontMissing,
    OUTLINE_MAX_PX,
    band_box,
    caption_line,
    check_caption_font,
    render_caption_page,
)
from app.services.export import compose_page
from app.services.layout import Layout, PaperSize, PrintMode, compute_layout
from app.services.lineart_render import LINE_WIDTH_DEFAULT as LINE_W
from tests.conftest import REPO
from tests.fake_engine import FakeEngine
from tests.test_api_lineart import color_png, make_passed, page_out, seed_generation, set_page
from tests.test_api_pages import book_with_character, new_page

FONT = REPO / "assets" / "fonts" / "NotoSansTC-VF.ttf"
A4_HOME = compute_layout(PaperSize.A4, PrintMode.HOME)
A4_SHOP = compute_layout(PaperSize.A4, PrintMode.PRINT_SHOP)
A5_HOME = compute_layout(PaperSize.A5, PrintMode.HOME)


def legacy_compose(ink: np.ndarray, layout: Layout) -> Image.Image:
    """Phase 11 之前的 compose_page（逐字複製），用來確認沒有文字的頁面一個像素都沒變。"""
    content = Image.fromarray(np.where(ink, 0, 255).astype(np.uint8), mode="L").convert(
        "1", dither=Image.Dither.NONE
    )
    page = Image.new("1", layout.page_px, 1)
    page.paste(content, layout.content_offset_px)
    return page


def synthetic_art(layout: Layout, line_w: int = LINE_W, gap: int = 0) -> np.ndarray:
    """內容大小的線稿，線寬剛好 line_w px：外框 + 方格 + 兩個圓環；gap > 0 時在第一個圓環右側切一個缺口。

    （不用 cv2.line／rectangle：thickness=11 實際會畫成 13 px。）
    """
    w, h = layout.content_px
    ink = np.zeros((h, w), bool)
    m = 40
    xs = list(range(m, w - m, w // 4)) + [w - m - line_w]
    ys = list(range(m, h - m, h // 5)) + [h - m - line_w]
    for x in xs:
        ink[m:h - m, x:x + line_w] = True
    for y in ys:
        ink[y:y + line_w, m:w - m] = True
    yy, xx = np.ogrid[:h, :w]
    for cx, cy in ((w // 3, h // 3), (2 * w // 3, 2 * h // 3)):
        d = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
        ink |= (d >= 150) & (d < 150 + line_w)
    if gap:
        cx, cy = w // 3 + 150, h // 3  # 第一個圓環的最右側
        ink[cy - gap // 2:cy + gap - gap // 2, cx - 2:cx + line_w + 2] = False
    return ink


def content_of(page: Image.Image, layout: Layout) -> np.ndarray:
    """頁面 → 內容區的 ink（True＝黑）。"""
    x, y = layout.content_offset_px
    w, h = layout.content_px
    return ~np.asarray(page.crop((x, y, x + w, y + h)))


def bbox(mask: np.ndarray) -> tuple[int, int, int, int]:
    ys, xs = np.nonzero(mask)
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def stroke_px(ink: np.ndarray) -> float:
    """線寬的保守估計：骨架上距離值 ×2−1 的中位數（奇數線寬剛好，偶數線寬少算 1）。"""
    dist = cv2.distanceTransform(ink.astype(np.uint8), cv2.DIST_L2, 5)
    return float(np.median(dist[skeletonize(ink)]) * 2 - 1)


def text_only(layout: Layout, zh: str | None, en: str | None) -> np.ndarray:
    blank = np.zeros(layout.content_px[::-1], dtype=bool)
    return content_of(render_caption_page(blank, layout, zh, en, FONT), layout)


def enclosed_white(region: np.ndarray, min_area: int = 50) -> list[int]:
    """region（True＝黑）裡被黑色包住（不碰邊）的白色區塊面積。"""
    n, lab, stats, _ = cv2.connectedComponentsWithStats((~region).astype(np.uint8), connectivity=4)
    h, w = region.shape
    out = []
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        if x > 0 and y > 0 and x + bw < w and y + bh < h and area >= min_area:
            out.append(int(area))
    return out


# --- 沒有文字：一個像素都不變 -------------------------------------------------------------

@pytest.mark.parametrize("layout", [A4_HOME, A4_SHOP], ids=["a4-home", "a4-shop"])
def test_no_captions_is_byte_identical(layout):
    ink = synthetic_art(layout)
    want = legacy_compose(ink, layout).tobytes()
    assert compose_page(ink, layout).tobytes() == want
    assert compose_page(ink, layout, (None, None), FONT).tobytes() == want
    assert compose_page(ink, layout, ("", "  "), FONT).tobytes() == want
    # 沒有文字時不需要字型
    assert compose_page(ink, layout, (None, None), Path("/nope/missing.ttf")).tobytes() == want


# --- 版面：文字帶、線稿縮放與置中 ---------------------------------------------------------

def test_band_is_bottom_15_percent_of_content_in_home_mode():
    w, h = A4_HOME.content_px
    assert band_box(A4_HOME) == (0, h - round(h * 0.15), w, h)


@pytest.mark.parametrize("layout", [A4_HOME, A4_SHOP], ids=["a4-home", "a4-shop"])
def test_band_holds_text_and_no_art(layout):
    ink = synthetic_art(layout)
    full = content_of(render_caption_page(ink, layout, "消防車", "Fire Engine", FONT), layout)
    text = text_only(layout, "消防車", "Fire Engine")
    x0, y0, x1, y1 = band_box(layout)
    assert text[y0:y1, x0:x1].any()  # 文字在帶裡
    assert not text[:y0].any() and not text[y1:].any()  # 文字不在帶外
    # 帶內（含整條帶的寬度）只有文字，沒有任何線稿像素
    assert np.array_equal(full[y0:, :], text[y0:, :])


@pytest.mark.parametrize("layout", [A4_HOME, A4_SHOP, A5_HOME], ids=["a4-home", "a4-shop", "a5-home"])
def test_art_sits_above_band_and_is_centred(layout):
    ink = synthetic_art(layout)
    full = content_of(render_caption_page(ink, layout, "消防車", "Fire Engine", FONT), layout)
    _x0, y0, _x1, _y1 = band_box(layout)
    art = full[:y0]
    ax0, ay0, ax1, ay1 = bbox(art)
    assert ay1 <= y0
    w = layout.content_px[0]
    assert abs(ax0 - (w - ax1)) <= 2  # 左右留白相同
    # 等比例縮放：外框的長寬比不變（原圖外框 40 px 內縮）
    ox0, oy0, ox1, oy1 = bbox(ink)
    assert abs((ax1 - ax0) / (ay1 - ay0) - (ox1 - ox0) / (oy1 - oy0)) < 0.01
    # 縮到剛好填滿帶上方的高度
    assert (ay1 - ay0) / (oy1 - oy0) == pytest.approx(y0 / layout.content_px[1], abs=0.01)


def test_print_shop_band_and_text_inside_safe_box():
    sx0, sy0, sx1, sy1 = A4_SHOP.safe_box_px
    x0, y0, x1, y1 = band_box(A4_SHOP)
    assert sx0 <= x0 < x1 <= sx1 and sy0 <= y0 < y1 <= sy1
    assert y1 - y0 == round(A4_SHOP.content_px[1] * 0.15)
    for zh, en in (("消防車", "Fire Engine"), ("消防車消防車消防車消防車", "W" * 28), (None, "Helicopter")):
        tx0, ty0, tx1, ty1 = bbox(text_only(A4_SHOP, zh, en))
        assert sx0 <= tx0 and tx1 <= sx1 and y0 <= ty0 and ty1 <= y1, (zh, en)


# --- 縮放後的線寬與缺口 -----------------------------------------------------------------

@pytest.mark.parametrize("layout", [A4_HOME, A4_SHOP, A5_HOME], ids=["a4-home", "a4-shop", "a5-home"])
@pytest.mark.parametrize("line_w", [LINE_W, 12, 16])
def test_scaled_art_keeps_stroke_width(layout, line_w):
    ink = synthetic_art(layout, line_w)
    full = content_of(render_caption_page(ink, layout, "消防車", "Fire Engine", FONT), layout)
    art = full[:band_box(layout)[1]]
    assert stroke_px(art) >= 10


def test_scale_art_dilates_when_resampled_lines_get_thin():
    """11 px 線縮到 85%：單純 LANCZOS＋128 閾值只剩約 9 px → 膨脹 1 px 回到 ≥ 10。"""
    from app.services.captions import scale_art

    ink = synthetic_art(A4_HOME, LINE_W)
    assert stroke_px(ink) == LINE_W
    h, w = ink.shape
    s = band_box(A4_HOME)[1] / h
    size = (round(w * s), round(h * s))
    raw = np.asarray(Image.fromarray(np.where(ink, 0, 255).astype(np.uint8)).resize(
        size, Image.Resampling.LANCZOS)) < 128
    assert stroke_px(raw) < 10
    assert stroke_px(scale_art(ink, size)) >= 10


def test_thin_art_is_dilated_once():
    """使用者選的最細線寬 8 px：縮小後只膨脹 1 px（spec §4.6），不會比原本細、也不會被加粗到 10 以上。"""
    ink = synthetic_art(A4_HOME, 8)
    full = content_of(render_caption_page(ink, A4_HOME, "消防車", None, FONT), A4_HOME)
    art = full[:band_box(A4_HOME)[1]]
    assert stroke_px(ink) <= stroke_px(art) < 10


def test_scaled_art_does_not_add_gaps():
    layout = A4_HOME
    ink = synthetic_art(layout, LINE_W, gap=6)
    full = content_of(render_caption_page(ink, layout, "消防車", "Fire Engine", FONT), layout)
    art = full[:band_box(layout)[1]]
    before = qa.evaluate(ink, LINE_W).gap_count
    assert before == 1  # 測試圖本身真的有一個缺口
    assert qa.evaluate(art, LINE_W).gap_count <= before


# --- 空心字 -----------------------------------------------------------------------------

@pytest.mark.parametrize("layout", [A4_HOME, A5_HOME], ids=["a4", "a5"])
def test_letters_are_hollow_and_counters_stay_white(layout):
    text = text_only(layout, "中", None)
    x0, y0, x1, y1 = band_box(layout)
    holes = enclosed_white(text[y0:y1, x0:x1])
    # 「中」：字身內部一塊白（空心）＋「口」的兩個字腔，都被黑色外框包住
    assert len(holes) >= 3, holes
    tx0, ty0, tx1, ty1 = bbox(text)
    glyph = text[ty0:ty1, tx0:tx1]
    assert glyph.mean() < 0.5  # 大部分是白的：外框，不是實心字


def band_sizes(layout: Layout) -> tuple[int, int]:
    """(中文字級, 英文字級)：帶高的 50%／30%。"""
    _x0, y0, _x1, y1 = band_box(layout)
    return round((y1 - y0) * 0.5), round((y1 - y0) * 0.3)


def counter_area(solid: np.ndarray) -> int:
    """實心字形裡被字形包住的白色（字腔）面積。"""
    return int(sum(enclosed_white(solid, min_area=1)))


@pytest.mark.parametrize("layout", [A4_HOME, A5_HOME], ids=["a4", "a5"])
@pytest.mark.parametrize("ch", ["警", "機"])
def test_outline_grows_inward_only_so_counters_stay_white(layout, ch):
    zh_size, _ = band_sizes(layout)
    outline, solid, s = caption_line(ch, zh_size, FONT)
    assert 4 <= s <= OUTLINE_MAX_PX
    assert not (outline & ~solid).any()  # 外框完全在字形內：字腔、字外一個像素都沒變黑
    assert (solid & ~outline).sum() > 0.2 * solid.sum()  # 字身內部大量留白（空心）
    # 實際印在頁面上：字的範圍內白色面積 ≥ 原字形的字腔面積
    text = text_only(layout, ch, None)
    tx0, ty0, tx1, ty1 = bbox(text)
    white_in_box = int((~text[ty0:ty1, tx0:tx1]).sum())
    assert white_in_box >= counter_area(solid) > 0
    assert text[ty0:ty1, tx0:tx1].shape == solid.shape


@pytest.mark.parametrize("layout", [A4_HOME, A5_HOME], ids=["a4", "a5"])
def test_adjacent_english_letters_do_not_touch(layout):
    _, en_size = band_sizes(layout)
    outline, solid, _s = caption_line("Ambulance", en_size, FONT)
    n_letters, letters = cv2.connectedComponents(solid.astype(np.uint8), connectivity=8)
    assert n_letters - 1 == len("Ambulance")  # 字形本身每個字母各自獨立
    n_parts, parts = cv2.connectedComponents(outline.astype(np.uint8), connectivity=8)
    for i in range(1, n_parts):
        owners = set(np.unique(letters[parts == i])) - {0}
        assert len(owners) == 1  # 每一段外框只屬於一個字母


def test_thin_strokes_stay_hollow_at_the_smallest_band():
    """A5 帶的英文（約 101 px）：大部分筆畫中間仍留白，不會整筆塗黑。"""
    from skimage.morphology import skeletonize as sk_

    _, en_size = band_sizes(A5_HOME)
    for text in ("Fire Engine", "Ambulance", "Helicopter"):
        outline, solid, _s = caption_line(text, en_size, FONT)
        skel = sk_(solid)
        assert (~outline[skel]).mean() >= 0.8, text


def test_output_is_one_bit_page():
    page = render_caption_page(synthetic_art(A4_SHOP), A4_SHOP, "消防車", "Fire Engine", FONT)
    assert page.mode == "1" and page.size == A4_SHOP.page_px
    via_compose = compose_page(synthetic_art(A4_SHOP), A4_SHOP, ("消防車", "Fire Engine"), FONT)
    assert via_compose.tobytes() == page.tobytes()


# --- 縮字與單行置中 ---------------------------------------------------------------------

@pytest.mark.parametrize("layout", [A4_HOME, A5_HOME, A4_SHOP], ids=["a4", "a5", "a4-shop"])
def test_long_lines_shrink_to_90_percent_of_content_width(layout):
    w = layout.content_px[0]
    for zh, en in ((None, "W" * 28), ("消防車消防車消防車消防車", None), ("消防車消防車消防車消防車", "W" * 28)):
        tx0, _ty0, tx1, _ty1 = bbox(text_only(layout, zh, en))
        assert tx1 - tx0 <= 0.9 * w, (zh, en)
        assert abs(tx0 - (w - tx1)) <= 2  # 水平置中


def test_long_english_line_is_shrunk_not_cropped():
    both_short = bbox(text_only(A4_HOME, None, "WW"))
    long_ = bbox(text_only(A4_HOME, None, "W" * 28))
    assert long_[3] - long_[1] < both_short[3] - both_short[1]  # 字變小了


@pytest.mark.parametrize("zh,en", [("消防車", None), (None, "Fire Engine")])
def test_single_caption_is_centred_in_band_at_chinese_size(zh, en):
    layout = A4_HOME
    _x0, y0, _x1, y1 = band_box(layout)
    tx0, ty0, tx1, ty1 = bbox(text_only(layout, zh, en))
    assert abs((ty0 + ty1) / 2 - (y0 + y1) / 2) <= 2
    if en:
        _a, ey0, _b, ey1 = bbox(text_only(layout, "消防車", en)[y0 + round(0.59 * (y1 - y0)):])
        assert (ty1 - ty0) > 1.4 * (ey1 - ey0)  # 單行英文用中文的大小


def test_two_lines_sit_at_30_and_78_percent():
    layout = A4_HOME
    _x0, y0, _x1, y1 = band_box(layout)
    bh = y1 - y0
    text = text_only(layout, "中", "HI")
    mid = y0 + round(0.59 * bh)  # 中文行的底（約 55%）與英文行的頂（約 62%）之間
    _a, zy0, _b, zy1 = bbox(text[:mid])
    _a, ey0, _b, ey1 = bbox(text[mid:])
    assert abs((zy0 + zy1) / 2 - (y0 + 0.30 * bh)) <= 3
    assert abs((ey0 + ey1) / 2 + mid - (y0 + 0.78 * bh)) <= 3


# --- 字型 -------------------------------------------------------------------------------

def test_missing_font_with_captions_raises(tmp_path):
    ink = synthetic_art(A5_HOME)
    with pytest.raises(CaptionFontMissing) as e:
        compose_page(ink, A5_HOME, ("消防車", None), tmp_path / "missing.ttf")
    assert str(e.value) == CAPTION_FONT_MISSING == "找不到字型檔，無法印出頁面文字"
    with pytest.raises(CaptionFontMissing):
        compose_page(ink, A5_HOME, (None, "Bus"), None)
    bad = tmp_path / "bad.ttf"
    bad.write_bytes(b"not a font")
    with pytest.raises(CaptionFontMissing):
        compose_page(ink, A5_HOME, ("消防車", None), bad)


def test_check_caption_font(tmp_path):
    assert check_caption_font(FONT) is None
    msg = check_caption_font(tmp_path / "missing.ttf")
    assert msg and "missing.ttf" in msg
    bad = tmp_path / "bad.ttf"
    bad.write_bytes(b"not a font")
    assert check_caption_font(bad)



# --- API：print.png、擁有者 PDF、分享圖與分享 PDF 都經過 composed_page ----------------------

@pytest.fixture
def app(settings_file):
    return create_app(load_settings(settings_file), engine=FakeEngine(), start_worker=False,
                      startup_preflight=False)


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


def passed_page(client, app, **book):
    app.state.engine.image = color_png()
    b, _ = book_with_character(client, app, **book)
    p = new_page(client, b["id"])
    set_page(app, p["id"], selected_generation_id=seed_generation(app, b["id"], p["id"], color_png()))
    make_passed(app, p["id"])
    return b, page_out(client, b["id"], p["id"])


def caption(client, b, p, zh, en):
    r = client.patch(f"/api/pages/{p['id']}", json={"caption_zh": zh, "caption_en": en})
    assert r.status_code == 200, r.text
    return r.json()


def page_png(client, p) -> Image.Image:
    r = client.get(f"/api/pages/{p['id']}/print.png")
    assert r.status_code == 200, r.text
    return Image.open(io.BytesIO(r.content))


def test_settings_default_font_is_the_committed_file(app, client):
    assert app.state.settings.paths.caption_font == FONT


def test_print_png_has_band_and_keeps_status(client, app):
    b, p = passed_page(client, app, paper_size="a4", print_mode="print_shop")
    plain = page_png(client, p)
    caption(client, b, p, "消防車", "Fire Engine")
    im = page_png(client, p)
    assert im.mode == "1" and im.size == A4_SHOP.page_px
    assert im.tobytes() != plain.tobytes()
    x0, y0, x1, y1 = band_box(A4_SHOP)
    got = content_of(im, A4_SHOP)
    assert np.array_equal(got[y0:, :], text_only(A4_SHOP, "消防車", "Fire Engine")[y0:, :])
    assert page_out(client, b["id"], p["id"])["status"] == "passed"  # 文字不影響 QA／狀態
    caption(client, b, p, "", None)  # 清掉 → 回到原本的樣子
    assert page_png(client, p).tobytes() == plain.tobytes()


def test_missing_font_is_409_only_with_captions(client, app, tmp_path):
    b, p = passed_page(client, app, paper_size="a5", print_mode="home")
    s = app.state.settings
    app.state.settings = s.model_copy(update={"paths": s.paths.model_copy(
        update={"caption_font": tmp_path / "missing.ttf"})})
    assert client.get(f"/api/pages/{p['id']}/print.png").status_code == 200  # 沒有文字：不需要字型
    assert client.get(f"/api/books/{b['id']}/export.pdf").status_code == 200
    caption(client, b, p, None, "Bus")
    for url in (f"/api/pages/{p['id']}/print.png", f"/api/books/{b['id']}/export.pdf"):
        r = client.get(url)
        assert r.status_code == 409 and r.json()["detail"] == "找不到字型檔，無法印出頁面文字", url


def test_book_pdf_and_shared_views_carry_captions(client, app, monkeypatch):
    b, p = passed_page(client, app, paper_size="a5", print_mode="home")
    caption(client, b, p, "火車", "Train")
    seen = []
    real = export_api.compose_page

    def spy(ink, layout, captions=(None, None), font_path=None):
        seen.append((captions, font_path))
        return real(ink, layout, captions, font_path)

    monkeypatch.setattr(export_api, "compose_page", spy)
    owner_png = page_png(client, p)
    assert client.get(f"/api/books/{b['id']}/export.pdf").status_code == 200
    assert client.put(f"/api/books/{b['id']}/share", json={"shared": True}).status_code == 200
    r = client.get(f"/api/shared/pages/{p['id']}/image.png")
    assert r.status_code == 200
    # 分享圖＝同一張合成頁（含文字帶）縮到 1600 px
    small = owner_png.convert("L")
    small.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
    assert np.array_equal(np.asarray(Image.open(io.BytesIO(r.content))), np.asarray(small))
    r = client.get(f"/api/shared/books/{b['id']}/export.pdf")
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
    assert len(seen) == 4 and all(c == (("火車", "Train"), FONT) for c in seen)


def test_shared_views_404_when_font_missing(client, app, tmp_path):
    b, p = passed_page(client, app, paper_size="a5", print_mode="home")
    caption(client, b, p, "火車", None)
    assert client.put(f"/api/books/{b['id']}/share", json={"shared": True}).status_code == 200
    s = app.state.settings
    app.state.settings = s.model_copy(update={"paths": s.paths.model_copy(
        update={"caption_font": tmp_path / "missing.ttf"})})
    assert client.get(f"/api/shared/pages/{p['id']}/image.png").status_code == 404
    assert client.get(f"/api/shared/books/{b['id']}/export.pdf").status_code == 404


def test_font_is_loaded_once_per_size():
    captions_mod.load_font.cache_clear()
    render_caption_page(np.zeros(A5_HOME.content_px[::-1], bool), A5_HOME, "火車", "Train", FONT)
    render_caption_page(np.zeros(A5_HOME.content_px[::-1], bool), A5_HOME, "飛機", "Plane", FONT)
    info = captions_mod.load_font.cache_info()
    assert info.misses == 2 and info.hits >= 2  # 兩種字級各載入一次
