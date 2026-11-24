"""頁面合成的肖像模式（放進框裡）：裁進框的內圈、autofit 寫回頁面位置、找不到框時照一般合成。"""

from __future__ import annotations

import io
import json

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlmodel import col, select

from app.config import load_settings
from app.main import create_app
from app.models import PageHistory
from app.services.compose import cutout
from app.services.frame_fit import find_interior, fit_portrait
from tests.fake_engine import FakeEngine
from tests.test_api_compose import composites, confirm, gen_pose, gen_row, picked, png_bytes, upload_bg
from tests.test_api_lineart import A5_HOME, color_png, get_page
from tests.test_api_pages import book_with_character, new_page

SEED = {"x": 0.5, "y": 0.55}


@pytest.fixture
def app(settings_file):
    return create_app(load_settings(settings_file), engine=FakeEngine(), start_worker=False)


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


def ring_png(axes=(300, 480), size=(832, 1216)) -> bytes:
    """白紙上一個橢圓框（線寬 8 px），中心在 (w/2, 0.49h)。"""
    w, h = size
    arr = np.full((h, w, 3), 255, np.uint8)
    cv2.ellipse(arr, (w // 2, round(h * 0.49)), axes, 0, 0, 360, (0, 0, 0), 8)
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, "PNG")
    return buf.getvalue()


def portrait_page(client, app, bg_png: bytes):
    """建書＋角色＋一頁：產生並選用一個角色動作，背景用上傳的圖。回傳頁面 JSON。"""
    app.state.engine.image = color_png()
    b, _ = book_with_character(client, app, **A5_HOME)
    p = new_page(client, b["id"], scene_prompt="相框")
    picked(client, p["id"], "pose", gen_pose(client, app, p["id"]))
    r = upload_bg(client, p["id"], bg_png)
    assert r.status_code == 201, r.text
    return r.json()


def put_placement(client, page_id, **pl):
    r = client.put(f"/api/pages/{page_id}/placement", json=pl)
    assert r.status_code == 200, r.text
    return r.json()


def image(app, gen_id) -> Image.Image:
    with Image.open(io.BytesIO(app.state.storage.get(gen_row(app, gen_id).image_path))) as im:
        return im.convert("RGB")


def expected_fit(app, page) -> tuple[np.ndarray, tuple[float, float, float]]:
    """這頁背景的內圈，與角色動作放進去的肖像位置（與 compose_page 同樣的算法）。"""
    interior = find_interior(image(app, page["background_generation_id"]), (SEED["x"], SEED["y"]))
    assert interior is not None
    pose = gen_row(app, page["pose_generation_id"])
    with Image.open(io.BytesIO(app.state.storage.get(pose.image_path))) as im, \
            Image.open(io.BytesIO(app.state.storage.get(pose.mask_path))) as m:
        size = cutout(im, m).size
    return interior, fit_portrait(interior, size)


def stored_placement(app, page_id):
    raw = get_page(app, page_id).placement_json
    return json.loads(raw) if raw else None


def params(app, gen_id) -> dict:
    return json.loads(gen_row(app, gen_id).params_json)


def history_count(app, page_id) -> int:
    with app.state.sessions() as s:
        return len(s.exec(select(PageHistory.id).where(col(PageHistory.page_id) == page_id)).all())


def test_compose_page_autofit_writes_back(client, app):
    page = portrait_page(client, app, ring_png())
    pid = page["id"]
    put_placement(client, pid, cx=0.5, cy=0.66, height=0.6, flip=True, frame=SEED, autofit=True)
    snapshots = history_count(app, pid)
    out = confirm(client, pid)
    _, (cx, cy, height) = expected_fit(app, page)
    pl = stored_placement(app, pid)
    assert pl["frame"] == SEED and pl["autofit"] is True and pl["flip"] is True
    assert (pl["cx"], pl["cy"], pl["height"]) == pytest.approx((cx, cy, height), abs=1e-4)
    assert out["placement"]["height"] == pytest.approx(height, abs=1e-4)
    assert height > 1.0  # 肖像比整頁高（框外裁掉）
    p = params(app, out["selected_generation_id"])
    assert p["frame_found"] is True and p["placement"] == pl
    # 寫回只改位置欄位：不存復原快照、不動等著寫回的筆畫
    assert history_count(app, pid) == snapshots
    assert get_page(app, pid).pending_user_strokes is None
    # 再確認一次：位置算出來一樣，直接選回同一張合成圖
    again = confirm(client, pid)
    assert again["selected_generation_id"] == out["selected_generation_id"]
    assert len(composites(app, pid)) == 1


def test_compose_page_no_frame_keeps_settings_and_clamps(client, app):
    page = portrait_page(client, app, png_bytes(color="white"))  # 白紙：沒有框
    pid = page["id"]
    put_placement(client, pid, cx=0.5, cy=0.6, height=1.6, flip=False, frame=SEED, autofit=True)
    before = get_page(app, pid).placement_json
    out = confirm(client, pid)
    p = params(app, out["selected_generation_id"])
    assert p["frame_found"] is False
    assert p["placement"]["height"] == 1.0 and p["placement"]["frame"] == SEED
    assert get_page(app, pid).placement_json == before  # frame、autofit、height 1.6 都留著（換背景會再試）
    assert confirm(client, pid)["selected_generation_id"] == out["selected_generation_id"]


def test_compose_page_new_background_refits(client, app):
    page = portrait_page(client, app, ring_png(axes=(300, 480)))
    pid = page["id"]
    put_placement(client, pid, cx=0.5, cy=0.66, height=0.6, flip=False, frame=SEED, autofit=True)
    confirm(client, pid)
    big = stored_placement(app, pid)["height"]
    page = upload_bg(client, pid, ring_png(axes=(200, 300))).json()  # 換一張框比較小的背景
    out = confirm(client, pid)
    _, (_, _, small) = expected_fit(app, page)
    pl = stored_placement(app, pid)
    assert pl["height"] == pytest.approx(small, abs=1e-4) and small < big - 0.2
    assert pl["autofit"] is True and pl["frame"] == SEED
    assert params(app, out["selected_generation_id"])["placement"]["height"] == pl["height"]


def test_compose_page_non_autofit_clips_with_stored_position(client, app):
    page = portrait_page(client, app, ring_png())
    pid = page["id"]
    put_placement(client, pid, cx=0.45, cy=0.6, height=1.5, flip=False, frame=SEED)
    before = get_page(app, pid).placement_json
    out = confirm(client, pid)
    assert get_page(app, pid).placement_json == before  # 沒有 autofit：位置照存的
    p = params(app, out["selected_generation_id"])
    assert p["frame_found"] is True and p["placement"] == json.loads(before)
    interior, _ = expected_fit(app, page)
    comp = np.asarray(image(app, out["selected_generation_id"]))
    bg = np.asarray(image(app, page["background_generation_id"]))
    assert np.array_equal(comp[~interior], bg[~interior])  # 框外（含框線）都沒有角色
    assert (comp[interior] != bg[interior]).any()


def test_compose_page_without_frame_params_unchanged(client, app):
    """沒開肖像的合成圖 params 沒有 frame_found（與今天相同）。"""
    page = portrait_page(client, app, ring_png())
    out = confirm(client, page["id"])
    assert "frame_found" not in params(app, out["selected_generation_id"])


# ---- frame-fit：舞台的「放進框裡」即時計算（不存任何東西）----

NO_FRAME = "這張背景找不到框，照一般擺放"


def test_frame_fit_returns_placement_and_saves_nothing(client, app):
    page = portrait_page(client, app, ring_png())
    pid = page["id"]
    put_placement(client, pid, cx=0.3, cy=0.6, height=0.5, flip=True)
    before = get_page(app, pid).placement_json
    r = client.post(f"/api/pages/{pid}/frame-fit")
    assert r.status_code == 200, r.text
    _, (cx, cy, height) = expected_fit(app, page)
    body = r.json()
    assert body["frame"] == SEED and body["autofit"] is True and body["flip"] is True
    assert (body["cx"], body["cy"], body["height"]) == pytest.approx((cx, cy, height))
    assert get_page(app, pid).placement_json == before  # 什麼都沒存
    assert composites(app, pid) == []


def test_frame_fit_422_when_no_frame(client, app):
    page = portrait_page(client, app, png_bytes(color="white"))
    r = client.post(f"/api/pages/{page['id']}/frame-fit")
    assert r.status_code == 422 and r.json()["detail"] == NO_FRAME


def test_frame_fit_needs_background_and_pose(client, app):
    app.state.engine.image = color_png()
    b, _ = book_with_character(client, app, **A5_HOME)
    p = new_page(client, b["id"], scene_prompt="相框")
    r = client.post(f"/api/pages/{p['id']}/frame-fit")
    assert r.status_code == 409 and r.json()["detail"] == "請先選好背景"
    assert upload_bg(client, p["id"], ring_png()).status_code == 201
    r = client.post(f"/api/pages/{p['id']}/frame-fit")
    assert r.status_code == 409 and r.json()["detail"] == "請先選好角色動作"
    assert client.post("/api/pages/9999/frame-fit").status_code == 404
