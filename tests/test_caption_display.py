"""Phase 13b：每頁選要印哪幾行圖下方的字（page.caption_show_zh／caption_show_en）。

- 兩個都開（預設）：和以前一模一樣（沒有字就沒有文字帶、線稿全尺寸）；
- 只開一個：只印開著而且有字的那一行；開著的那行沒字 → 那行不印（和以前一樣）；
- 兩個都關：文字帶照留、整條空白（給小朋友自己寫），線稿不放大回全尺寸——沒有字的頁也一樣。
- POST /api/books/{id}/caption-display：整本書套用同一個選擇（不動復原紀錄）。
- C 的 PATCH ?oneclick=true 改開關先存復原快照，復原／重做還原開關。
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.api import export as export_api
from app.config import load_settings
from app.main import create_app
from app.models import Page, PageHistory
from app.services.captions import band_box, render_caption_page
from app.services.export import compose_page
from sqlmodel import col, select
from tests.fake_engine import FakeEngine
from tests.test_api_history import C, history_rows, page_json, ready, redo, undo
from tests.test_api_lineart import get_page
from tests.test_captions import (
    A4_HOME, A4_SHOP, A5_HOME, FONT, bbox, content_of, legacy_compose, passed_page, synthetic_art,
)
from tests.test_pipeline import queued

ZH, EN = "消防車", "Fire Engine"
MISSING = Path("/nope/missing.ttf")


def art_above(page: Image.Image, layout) -> np.ndarray:
    _x0, y0, _x1, _y1 = band_box(layout)
    return content_of(page, layout)[:y0]


# --- compose_page ----------------------------------------------------------------------

@pytest.mark.parametrize("layout", [A4_HOME, A4_SHOP], ids=["a4-home", "a4-shop"])
def test_both_on_is_unchanged(layout):
    ink = synthetic_art(layout)
    assert compose_page(ink, layout, (None, None), FONT, show=(True, True)).tobytes() == \
        legacy_compose(ink, layout).tobytes()
    assert compose_page(ink, layout, (ZH, EN), FONT, show=(True, True)).tobytes() == \
        compose_page(ink, layout, (ZH, EN), FONT).tobytes() == \
        render_caption_page(ink, layout, ZH, EN, FONT).tobytes()


@pytest.mark.parametrize("layout", [A4_HOME, A4_SHOP], ids=["a4-home", "a4-shop"])
def test_only_enabled_lines_print(layout):
    ink = synthetic_art(layout)
    only_zh = compose_page(ink, layout, (ZH, EN), FONT, show=(True, False))
    only_en = compose_page(ink, layout, (ZH, EN), FONT, show=(False, True))
    assert only_zh.tobytes() == compose_page(ink, layout, (ZH, None), FONT).tobytes()
    assert only_en.tobytes() == compose_page(ink, layout, (None, EN), FONT).tobytes()
    assert only_zh.tobytes() != only_en.tobytes()


def test_enabled_line_without_text_prints_nothing_for_that_line():
    """開著的那行沒有字：那行不印；另一行關著 → 沒有要印的字，和沒有字的頁一樣（沒有文字帶）。"""
    ink = synthetic_art(A4_HOME)
    assert compose_page(ink, A4_HOME, (None, EN), FONT, show=(True, False)).tobytes() == \
        legacy_compose(ink, A4_HOME).tobytes()
    assert compose_page(ink, A4_HOME, ("", EN), FONT, show=(True, True)).tobytes() == \
        compose_page(ink, A4_HOME, (None, EN), FONT).tobytes()


@pytest.mark.parametrize("layout", [A4_HOME, A4_SHOP, A5_HOME], ids=["a4-home", "a4-shop", "a5-home"])
@pytest.mark.parametrize("captions", [(ZH, EN), (None, None), ("", " ")], ids=["text", "none", "blank"])
def test_both_off_reserves_an_empty_band(layout, captions):
    ink = synthetic_art(layout)
    page = compose_page(ink, layout, captions, FONT, show=(False, False))
    assert page.mode == "1" and page.size == layout.page_px
    got = content_of(page, layout)
    x0, y0, x1, y1 = band_box(layout)
    assert not got[y0:].any()  # 文字帶（以及帶下方）整條空白：沒有字、沒有寫字線、沒有線稿
    # 線稿和有字的頁一樣縮到帶的上方，不放大回全尺寸
    with_text = compose_page(ink, layout, (ZH, EN), FONT)
    assert np.array_equal(got[:y0], art_above(with_text, layout))
    assert page.tobytes() != legacy_compose(ink, layout).tobytes()
    _ax0, _ay0, _ax1, ay1 = bbox(got)
    assert ay1 <= y0


def test_both_off_needs_no_font():
    ink = synthetic_art(A5_HOME)
    want = compose_page(ink, A5_HOME, (ZH, EN), FONT, show=(False, False)).tobytes()
    assert compose_page(ink, A5_HOME, (ZH, EN), MISSING, show=(False, False)).tobytes() == want
    assert compose_page(ink, A5_HOME, (None, None), None, show=(False, False)).tobytes() == want


def test_both_off_print_shop_band_is_inside_safe_box_and_blank():
    layout = A4_SHOP
    ink = synthetic_art(layout)
    page = compose_page(ink, layout, (ZH, EN), FONT, show=(False, False))
    got = content_of(page, layout)
    sx0, sy0, sx1, sy1 = layout.safe_box_px
    x0, y0, x1, y1 = band_box(layout)
    assert sx0 <= x0 < x1 <= sx1 and sy0 <= y0 < y1 <= sy1
    assert not got[y0:y1, x0:x1].any()
    assert not got[y0:].any()  # 安全區下方的出血邊也是白的
    full = ~np.asarray(page)  # 整頁：版面外（內容區外）也沒有任何黑點
    ox, oy = layout.content_offset_px
    w, h = layout.content_px
    outside = full.copy()
    outside[oy:oy + h, ox:ox + w] = False
    assert not outside.any()


# --- API -------------------------------------------------------------------------------

@pytest.fixture
def app(settings_file):
    return create_app(load_settings(settings_file), engine=FakeEngine(), start_worker=False,
                      startup_preflight=False)


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


def png(client, pid) -> Image.Image:
    r = client.get(f"/api/pages/{pid}/print.png")
    assert r.status_code == 200, r.text
    return Image.open(io.BytesIO(r.content))


def patch(client, pid, params=None, **body):
    r = client.patch(f"/api/pages/{pid}", params=params or {}, json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_page_out_defaults_to_both_on(client, app):
    b, p = passed_page(client, app, paper_size="a5", print_mode="home")
    assert p["caption_show_zh"] is True and p["caption_show_en"] is True
    ov = client.get(f"/api/books/{b['id']}/overview").json()["pages"][0]
    assert ov["caption_show_zh"] is True and ov["caption_show_en"] is True


def test_patch_flags_change_print_png(client, app):
    b, p = passed_page(client, app, paper_size="a5", print_mode="home")
    pid = p["id"]
    plain = png(client, pid)
    patch(client, pid, caption_zh=ZH, caption_en=EN)
    both = png(client, pid)
    out = patch(client, pid, caption_show_en=False)
    assert out["caption_show_en"] is False and out["caption_show_zh"] is True
    assert out["caption_en"] == EN  # 文字留著，只是不印
    only_zh = png(client, pid)
    assert only_zh.tobytes() not in (both.tobytes(), plain.tobytes())
    out = patch(client, pid, caption_show_zh=False)
    assert out["caption_show_zh"] is False
    blank = png(client, pid)
    assert blank.tobytes() not in (both.tobytes(), plain.tobytes(), only_zh.tobytes())
    x0, y0, x1, y1 = band_box(A5_HOME)
    got = content_of(blank, A5_HOME)
    assert not got[y0:].any() and got[:y0].any()
    assert np.array_equal(got[:y0], content_of(both, A5_HOME)[:y0])  # 線稿位置和有字時一樣
    # 沒有字的頁兩個都關也留白
    patch(client, pid, caption_zh=None, caption_en=None)
    assert png(client, pid).tobytes() == blank.tobytes()
    patch(client, pid, caption_show_zh=True, caption_show_en=True)
    assert png(client, pid).tobytes() == plain.tobytes()
    assert page_json(client, pid)["status"] == "passed"  # 開關不影響 QA／狀態


def test_flags_reach_pdf_and_shared_views(client, app, monkeypatch):
    b, p = passed_page(client, app, paper_size="a5", print_mode="home")
    patch(client, p["id"], caption_zh=ZH, caption_en=EN, caption_show_zh=False)
    seen = []
    real = export_api.compose_page

    def spy(ink, layout, captions=(None, None), font_path=None, show=(True, True)):
        seen.append(show)
        return real(ink, layout, captions, font_path, show=show)

    monkeypatch.setattr(export_api, "compose_page", spy)
    png(client, p["id"])
    assert client.get(f"/api/books/{b['id']}/export.pdf").status_code == 200
    assert client.put(f"/api/books/{b['id']}/share", json={"shared": True}).status_code == 200
    assert client.get(f"/api/shared/pages/{p['id']}/image.png").status_code == 200
    assert client.get(f"/api/shared/books/{b['id']}/export.pdf").status_code == 200
    assert seen == [(False, True)] * 4


def test_patch_without_flags_leaves_them(client, app):
    b, p = passed_page(client, app, paper_size="a5", print_mode="home")
    patch(client, p["id"], caption_show_zh=False)
    out = patch(client, p["id"], caption_zh="火車")
    assert out["caption_show_zh"] is False and out["caption_show_en"] is True


@pytest.mark.parametrize("bad", [None, "maybe", 2])
def test_patch_rejects_non_bool_flags(client, app, bad):
    b, p = passed_page(client, app, paper_size="a5", print_mode="home")
    r = client.patch(f"/api/pages/{p['id']}", json={"caption_show_zh": bad})
    assert r.status_code == 422, r.text


def test_oneclick_flag_change_pushes_snapshot_and_undo_redo_restore_it(client, app):
    b, page, _bg, _pose = ready(client, app)
    pid = page["id"]
    out = patch(client, pid, C, caption_show_en=False)
    assert out["can_undo"] is True and out["caption_show_en"] is False
    assert len(history_rows(app, pid)) == 1 and not queued(app, pid)  # 不用重新合成
    patch(client, pid, C, caption_show_zh=False)
    assert len(history_rows(app, pid)) == 2
    out = undo(client, pid)
    assert (out["caption_show_zh"], out["caption_show_en"]) == (True, False)
    out = undo(client, pid)
    assert (out["caption_show_zh"], out["caption_show_en"]) == (True, True)
    assert not queued(app, pid)
    out = redo(client, pid)
    assert (out["caption_show_zh"], out["caption_show_en"]) == (True, False)
    out = redo(client, pid)
    assert (out["caption_show_zh"], out["caption_show_en"]) == (False, False)


def test_unchanged_or_old_editor_flag_patch_pushes_nothing(client, app):
    b, page, _bg, _pose = ready(client, app)
    pid = page["id"]
    patch(client, pid, C, caption_show_zh=True)  # 沒變
    patch(client, pid, caption_show_en=False)  # 進階編輯（沒有 oneclick）
    assert history_rows(app, pid) == []


def test_old_snapshot_without_flags_restores_both_on(client, app):
    """Phase 13 的快照沒有開關欄位：復原時當成兩個都開（遷移前的頁面都是這樣）。"""
    b, page, _bg, _pose = ready(client, app)
    pid = page["id"]
    patch(client, pid, C, caption_zh="字")
    with app.state.sessions() as s:
        row = s.exec(select(PageHistory).where(col(PageHistory.page_id) == pid)).one()
        snap = json.loads(row.snapshot_json)
        snap.pop("caption_show_zh"), snap.pop("caption_show_en")
        row.snapshot_json = json.dumps(snap)
        s.add(row)
        pg = s.get(Page, pid)
        pg.caption_show_zh = pg.caption_show_en = False
        s.add(pg)
        s.commit()
    out = undo(client, pid)
    assert (out["caption_show_zh"], out["caption_show_en"]) == (True, True)


# --- 套用到整本書 -------------------------------------------------------------------------

def test_caption_display_applies_to_every_page(client, app):
    b, p = passed_page(client, app, paper_size="a5", print_mode="home")
    from tests.test_api_pages import new_page
    others = [new_page(client, b["id"]) for _ in range(2)]
    patch(client, others[0]["id"], caption_show_zh=False)
    r = client.post(f"/api/books/{b['id']}/caption-display", json={"show_zh": False, "show_en": True})
    assert r.status_code == 200, r.text
    pages = r.json()
    assert [x["id"] for x in pages] == [p["id"]] + [o["id"] for o in others]
    assert all(x["caption_show_zh"] is False and x["caption_show_en"] is True for x in pages)
    for pid in (p["id"], *(o["id"] for o in others)):
        pg = get_page(app, pid)
        assert (pg.caption_show_zh, pg.caption_show_en) == (False, True)
    assert all(history_rows(app, x["id"]) == [] for x in pages)  # 不動復原紀錄
    assert get_page(app, p["id"]).status.value == "passed"


def test_caption_display_leaves_other_books_alone(client, app):
    b1, p1 = passed_page(client, app, paper_size="a5", print_mode="home")
    b2, p2 = passed_page(client, app, paper_size="a5", print_mode="home")
    r = client.post(f"/api/books/{b1['id']}/caption-display", json={"show_zh": False, "show_en": False})
    assert r.status_code == 200
    pg = get_page(app, p2["id"])
    assert (pg.caption_show_zh, pg.caption_show_en) == (True, True)


@pytest.mark.parametrize("body", [{}, {"show_zh": True}, {"show_zh": "yes", "show_en": True},
                                  {"show_zh": None, "show_en": True}])
def test_caption_display_validates_body(client, app, body):
    b, _p = passed_page(client, app, paper_size="a5", print_mode="home")
    r = client.post(f"/api/books/{b['id']}/caption-display", json=body)
    assert r.status_code == 422, r.text


def test_caption_display_on_empty_book(client, app):
    from tests.test_api_books import new_book
    b = new_book(client)
    r = client.post(f"/api/books/{b['id']}/caption-display", json={"show_zh": True, "show_en": False})
    assert r.status_code == 200 and r.json() == []
