"""頁面合成流程 API：背景、角色動作、位置、確認合成。"""

import io
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlmodel import select

from app.config import load_settings
from app.main import create_app
from app.models import CharacterCheck, Generation, GenKind, GenStatus, Page, PageStatus
from app.services.compose import COMPOSE_VERSION, DEFAULT_PLACEMENT
from app.services.engine import EngineError
from app.services.layout import PaperSize, PrintMode, compute_layout
from app.services.prompts import (
    POSE_SIZE,
    QWEN_BACKGROUND_VARIATIONS,
    qwen_background_prompt,
    qwen_pose_prompt,
)
from tests.fake_engine import FakeEngine, make_png
from tests.test_api_books import new_book, run_jobs
from tests.test_api_lineart import (
    A5_HOME, DRAW, color_png, edits_body, get_page, legacy_page, make_passed, set_page,
)
from tests.test_api_pages import book_with_character, new_page


@pytest.fixture
def app(settings_file):
    return create_app(load_settings(settings_file), engine=FakeEngine(), start_worker=False)


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


def gen_background(client, app, page_id, *, run=True):
    r = client.post(f"/api/pages/{page_id}/backgrounds")
    assert r.status_code == 202, r.text
    if run:
        run_jobs(client, app)
    return r.json()["id"]


def gen_pose(client, app, page_id, action="waving hand", *, run=True):
    r = client.post(f"/api/pages/{page_id}/poses", json={"action": action})
    assert r.status_code == 202, r.text
    if run:
        run_jobs(client, app)
    return r.json()["id"]


def pick(client, page_id, what, gen_id):
    return client.post(f"/api/pages/{page_id}/{what}", json={"generation_id": gen_id})


def picked(client, page_id, what, gen_id):
    r = pick(client, page_id, what, gen_id)
    assert r.status_code == 200, r.text
    return r.json()


def compose_ready(client, app, book_kw=None):
    """建書＋角色＋一頁，產生並選用一張背景與一個角色動作。回傳 (book, page, bg id, pose id)。"""
    app.state.engine.image = color_png()
    b, _ = book_with_character(client, app, **(book_kw or A5_HOME))
    p = new_page(client, b["id"], scene_prompt="公園")
    bg = gen_background(client, app, p["id"])
    pose = gen_pose(client, app, p["id"])
    picked(client, p["id"], "background", bg)
    page = picked(client, p["id"], "pose", pose)
    return b, page, bg, pose


def confirm(client, page_id):
    r = client.post(f"/api/pages/{page_id}/compose")
    assert r.status_code == 200, r.text
    return r.json()


def gen_row(app, gen_id) -> Generation:
    with app.state.sessions() as s:
        return s.get(Generation, gen_id)


def composites(app, page_id) -> list[Generation]:
    with app.state.sessions() as s:
        return list(s.exec(select(Generation).where(
            Generation.page_id == page_id, Generation.kind == GenKind.COMPOSITE
        )).all())


def summary_reason(client, book_id, page_id):
    s = client.get(f"/api/books/{book_id}/export/summary").json()
    return next((x["reason"] for x in s["skipped"] if x["page_id"] == page_id), None)


# ---- 背景 ----------------------------------------------------------------------


def test_generate_and_pick_background(client, app):
    b = new_book(client)  # 背景不需要角色
    p = new_page(client, b["id"], scene_prompt="公園裡的大樹")
    r = client.post(f"/api/pages/{p['id']}/backgrounds")
    assert r.status_code == 202, r.text
    g = r.json()
    assert g["kind"] == "background" and g["status"] == "queued"
    assert g["user_text"] == "公園裡的大樹" and "公園裡的大樹" in g["prompt"]
    assert [g["width"], g["height"]] == b["layout"]["generation_size"]
    assert g["mask_url"] is None
    run_jobs(client, app)
    req = app.state.engine.requests[-1]
    assert req.kind == GenKind.BACKGROUND
    assert req.character_image is None
    assert (req.width, req.height) == tuple(b["layout"]["generation_size"])
    page = picked(client, p["id"], "background", g["id"])
    assert page["background_generation_id"] == g["id"]
    assert page["pose_generation_id"] is None and page["placement"] is None
    assert page["selected_generation_id"] is None
    assert client.post("/api/pages/999/backgrounds").status_code == 404


# ---- 背景（只有 Qwen 一種風格） -------------------------------------------------------


def _rj(app, gen_id) -> dict:
    return json.loads(gen_row(app, gen_id).request_json)


def _bg_post(client, page_id, body):
    if body is None:
        return client.post(f"/api/pages/{page_id}/backgrounds")
    return client.post(f"/api/pages/{page_id}/backgrounds", json=body)


@pytest.mark.parametrize("body", [None, {}])
def test_background_default_style_simple(client, app, body):
    p = new_page(client, new_book(client)["id"], scene_prompt="公園裡的大樹")
    r = _bg_post(client, p["id"], body)
    assert r.status_code == 202, r.text
    g = r.json()
    assert g["background_style"] == "simple"
    assert g["user_text"] == "公園裡的大樹"
    assert g["prompt"].startswith("公園裡的大樹. ")
    row = gen_row(app, g["id"])
    assert row.negative_prompt == ""
    rj = _rj(app, g["id"])
    assert rj["background_style"] == "simple"
    assert not {"background_style_requested", "structure_image", "structure_strength"} & set(rj)
    # 構圖變化語：每張隨機一個（校正過的清單），寫進 prompt 並記在 request_json
    assert rj["variation"] in QWEN_BACKGROUND_VARIATIONS
    assert g["prompt"] == qwen_background_prompt("公園裡的大樹", rj["variation"])
    assert rj["negative_prompt"] == ""
    run_jobs(client, app)
    req = app.state.engine.requests[-1]
    assert req.kind == GenKind.BACKGROUND and req.variation == rj["variation"]
    assert client.get(f"/api/generations/{g['id']}").json()["background_style"] == "simple"


@pytest.mark.parametrize("style", ["detailed", "simple", "qwen", None])
def test_background_ignores_style(client, app, style):
    """舊前端送的 style 一律忽略：只有簡潔（Qwen）一種。"""
    p = new_page(client, new_book(client)["id"], scene_prompt="公園")
    r = _bg_post(client, p["id"], {"style": style})
    assert r.status_code == 202, r.text
    g = r.json()
    assert g["background_style"] == "simple"
    rj = _rj(app, g["id"])
    assert rj["background_style"] == "simple" and rj["negative_prompt"] == ""
    assert g["prompt"] == qwen_background_prompt("公園", rj["variation"])
    assert gen_row(app, g["id"]).negative_prompt == ""


def qwen_missing_report(workflow):
    """快取的 preflight：這個 Qwen workflow 缺 GGUF 模型。"""
    from app.services.preflight import MissingModel, PreflightReport, WorkflowReport

    return PreflightReport(ok=False, comfy_url="http://x", comfy_reachable=True, workflows=[
        WorkflowReport(name=workflow, ok=False, version="1", missing_models=[
            MissingModel("qwen_edit_2511_q4km", "qwen-image-edit-2511-Q4_K_M.gguf", "unet", "沒有此檔案")]),
    ])


def with_sketch(app, book_id, page_id, *, file=True):
    """舊資料：頁面有構圖草圖（強度 0.4）；file=False 時草圖檔已遺失。"""
    rel = app.state.storage.sketch(book_id, page_id)
    if file:
        app.state.storage.put(rel, make_png((128, 128)))
    set_page(app, page_id, sketch_path=rel, controlnet_strength=0.4)


def test_background_ignores_sketch_and_preflight(client, app):
    """有草圖（強度 > 0）、草圖檔遺失、或快取的 preflight 說 qwen_background 缺模型：照樣排簡潔，
    不存草圖快照、不先擋（缺東西時由生成失敗回報）。"""
    b = new_book(client)
    p = new_page(client, b["id"], scene_prompt="公園")
    with_sketch(app, b["id"], p["id"])
    app.state.preflight = qwen_missing_report("qwen_background")
    g = _bg_post(client, p["id"], None).json()
    assert g["background_style"] == "simple"
    assert "structure_image" not in _rj(app, g["id"])
    assert not list(app.state.storage.root.rglob("sketch_*.png"))  # 沒有生成用的草圖快照
    app.state.storage.delete(app.state.storage.sketch(b["id"], p["id"]))
    r = _bg_post(client, p["id"], None)
    assert r.status_code == 202, r.text
    assert r.json()["background_style"] == "simple"


def test_background_simple_records_variation(client, app, monkeypatch):
    from app.api import compose as compose_api

    monkeypatch.setattr(
        compose_api, "pick_background_variation", lambda rng, exclude=None: "seen from far away"
    )
    p = new_page(client, new_book(client)["id"], scene_prompt="公園")
    g = _bg_post(client, p["id"], None).json()
    assert g["prompt"] == qwen_background_prompt("公園", "seen from far away")
    assert g["user_text"] == "公園"
    assert _rj(app, g["id"])["variation"] == "seen from far away"


def _set_status(app, gen_id, status, *, discarded=False):
    with app.state.sessions() as s:
        row = s.get(Generation, gen_id)
        row.status = status
        row.discarded = discarded
        s.add(row)
        s.commit()


def _make_old_detailed(app, gen_id):
    """把一筆背景改成 Phase 9 以前的細緻（SDXL）舊列。"""
    with app.state.sessions() as s:
        row = s.get(Generation, gen_id)
        d = json.loads(row.request_json)
        d.update(background_style="detailed", variation=None)
        row.request_json = json.dumps(d)
        s.add(row)
        s.commit()


def test_background_simple_regenerate_never_repeats_variation(client, app):
    # 「再產生一張」：同一頁連續兩張簡潔背景的構圖變化語一定不同（排除上一張，不靠運氣）
    p = new_page(client, new_book(client)["id"], scene_prompt="公園")
    prev = None
    for _ in range(12):
        g = _bg_post(client, p["id"], None).json()
        v = _rj(app, g["id"])["variation"]
        assert v in QWEN_BACKGROUND_VARIATIONS and v != prev
        assert g["prompt"] == qwen_background_prompt("公園", v)
        prev = v


def test_background_simple_excludes_latest_simple_any_status(client, app, monkeypatch):
    from app.api import compose as compose_api

    calls: list[str | None] = []
    real = compose_api.pick_background_variation

    def spy(rng, exclude=None):
        calls.append(exclude)
        return real(rng, exclude=exclude)

    monkeypatch.setattr(compose_api, "pick_background_variation", spy)
    b = new_book(client)
    p = new_page(client, b["id"], scene_prompt="公園")
    other = new_page(client, b["id"], scene_prompt="公園")
    first = _bg_post(client, p["id"], None).json()
    assert calls == [None]  # 這頁還沒有簡潔背景
    v1 = _rj(app, first["id"])["variation"]
    # 失敗、丟棄的也算；中間夾一張細緻舊列不影響（只看最近一張簡潔）
    _set_status(app, first["id"], GenStatus.FAILED, discarded=True)
    _make_old_detailed(app, _bg_post(client, p["id"], None).json()["id"])
    second = _bg_post(client, p["id"], None).json()
    assert calls[-1] == v1
    v2 = _rj(app, second["id"])["variation"]
    assert v2 != v1
    # 只看同一頁
    _bg_post(client, other["id"], None)
    assert calls[-1] is None
    third = _bg_post(client, p["id"], None).json()
    assert calls[-1] == v2 and _rj(app, third["id"])["variation"] != v2


def test_batch_simple_excludes_latest_simple_per_page(client, app, monkeypatch):
    from app.api import compose as compose_api

    b = new_book(client)
    pages = [new_page(client, b["id"], scene_prompt=f"場景{i}") for i in range(2)]
    prior = _bg_post(client, pages[0]["id"], None).json()
    prior_v = _rj(app, prior["id"])["variation"]
    _set_status(app, prior["id"], GenStatus.FAILED)  # 失敗的頁面批次仍會排
    calls: dict[int, str | None] = {}
    real = compose_api.pick_background_variation
    order = iter(p["id"] for p in pages)

    def spy(rng, exclude=None):
        calls[next(order)] = exclude
        return real(rng, exclude=exclude)

    monkeypatch.setattr(compose_api, "pick_background_variation", spy)
    queued = batch(client, b["id"])["queued"]
    assert [g["page_id"] for g in queued] == [p["id"] for p in pages]
    assert calls == {pages[0]["id"]: prior_v, pages[1]["id"]: None}
    assert _rj(app, queued[0]["id"])["variation"] != prior_v


def test_batch_ignores_style(client, app):
    """批次：有草圖的頁面、草圖檔遺失的頁面、preflight 說缺模型，全都排簡潔（Qwen），不存草圖快照。"""
    b = new_book(client)
    pages = [new_page(client, b["id"], scene_prompt=f"場景{i}") for i in range(3)]
    with_sketch(app, b["id"], pages[1]["id"])
    with_sketch(app, b["id"], pages[2]["id"], file=False)  # 草圖檔遺失
    app.state.preflight = qwen_missing_report("qwen_background")
    scenes = {p["id"]: p["scene_prompt"] for p in pages}
    out = batch(client, b["id"])
    assert out["skipped"] == []
    assert [g["page_id"] for g in out["queued"]] == [p["id"] for p in pages]
    for g in out["queued"]:
        assert g["background_style"] == "simple"
        rj = _rj(app, g["id"])
        assert rj["background_style"] == "simple" and rj["negative_prompt"] == ""
        assert "structure_image" not in rj
        assert g["prompt"] == qwen_background_prompt(scenes[g["page_id"]], rj["variation"])
    assert not list(app.state.storage.root.rglob("sketch_*.png"))  # 沒有生成用的草圖快照


def test_generation_out_background_style_old_row_detailed(client, app):
    b, _ = book_with_character(client, app)
    p = new_page(client, b["id"], scene_prompt="公園")
    old = gen_background(client, app, p["id"])
    with app.state.sessions() as s:  # 模擬 Phase 7 以前的舊列：request_json 沒有風格欄位
        row = s.get(Generation, old)
        d = json.loads(row.request_json)
        for k in ("background_style", "variation"):
            d.pop(k)
        row.request_json = json.dumps(d)
        s.add(row)
        s.commit()
    assert client.get(f"/api/generations/{old}").json()["background_style"] == "detailed"
    pose = gen_pose(client, app, p["id"])
    assert client.get(f"/api/generations/{pose}").json()["background_style"] is None
    queued = gen_background(client, app, p["id"], run=False)
    assert client.get(f"/api/generations/{queued}").json()["background_style"] == "simple"


def test_background_style_from_generation_tolerates_bad_request_json():
    from app.api.schemas import GenerationOut

    for rj in ("", "{oops", "[]", '{"background_style": "qwen"}'):
        g = Generation(id=1, book_id=1, page_id=1, kind=GenKind.BACKGROUND, user_text="", prompt="",
                       negative_prompt="", seed=0, width=8, height=8, request_json=rj)
        assert GenerationOut.from_generation(g).background_style == "detailed", rj


def test_old_rows_keep_badges(client, app):
    """Phase 9 以前的快速（SDXL）角色動作與細緻背景：GenerationOut 照舊標「快速」／「細緻」。"""
    b, _ = book_with_character(client, app)
    p = new_page(client, b["id"], scene_prompt="公園")
    pose = gen_pose(client, app, p["id"])
    bg = gen_background(client, app, p["id"])
    for gid, key, value in ((pose, "pose_mode", "fast"), (bg, "background_style", "detailed")):
        with app.state.sessions() as s:
            row = s.get(Generation, gid)
            row.request_json = json.dumps({key: value})
            s.add(row)
            s.commit()
    assert client.get(f"/api/generations/{pose}").json()["pose_mode"] == "fast"
    assert client.get(f"/api/generations/{bg}").json()["background_style"] == "detailed"
    from app.api.schemas import GenerationOut

    assert GenerationOut.from_generation(gen_row(app, pose)).pose_mode == "fast"


# ---- 角色動作 ------------------------------------------------------------------


def test_generate_and_pick_pose(client, app):
    b, char_gen = book_with_character(client, app)  # A4：生成尺寸 864×1216，動作仍固定 POSE_SIZE
    p = new_page(client, b["id"], scene_prompt="在廚房")
    r = client.post(f"/api/pages/{p['id']}/poses", json={"action": "  waving hand  "})
    assert r.status_code == 202, r.text
    g = r.json()
    assert g["kind"] == "pose" and g["user_text"] == "waving hand"
    assert (g["width"], g["height"]) == POSE_SIZE
    assert "waving hand" in g["prompt"] and "廚房" not in g["prompt"]
    run_jobs(client, app)
    req = app.state.engine.requests[-1]
    assert req.kind == GenKind.POSE and (req.width, req.height) == POSE_SIZE
    assert req.character_image == app.state.storage.get(gen_row(app, char_gen).image_path)
    g = client.get(f"/api/generations/{g['id']}").json()
    assert g["mask_url"] == f"/api/generations/{g['id']}/mask"
    m = client.get(g["mask_url"])
    assert m.status_code == 200 and m.headers["content-type"] == "image/png"
    assert Image.open(io.BytesIO(m.content)).format == "PNG"
    page = picked(client, p["id"], "pose", g["id"])
    assert page["pose_generation_id"] == g["id"]


def test_pose_action_length(client, app):
    b, _ = book_with_character(client, app)
    p = new_page(client, b["id"])
    for bad in ("", "   ", "跑" * 301):
        assert client.post(f"/api/pages/{p['id']}/poses", json={"action": bad}).status_code == 422
    assert client.post(f"/api/pages/{p['id']}/poses", json={}).status_code == 422
    assert client.post(f"/api/pages/{p['id']}/poses", json={"action": "跑" * 300}).status_code == 202


def test_pose_requires_character(client):
    p = new_page(client, new_book(client)["id"])
    r = client.post(f"/api/pages/{p['id']}/poses", json={"action": "running"})
    assert r.status_code == 409 and r.json()["detail"] == "請先設定角色"
    assert client.post("/api/pages/999/poses", json={"action": "x"}).status_code == 404


def request_of(app, gen_id) -> dict:
    return json.loads(gen_row(app, gen_id).request_json)


def test_pose_defaults_to_precise(client, app, monkeypatch):
    from app.api import compose as compose_api

    monkeypatch.setattr(compose_api, "pick_pose_variation", lambda rng: None)
    b, _ = book_with_character(client, app)
    p = new_page(client, b["id"])
    r = client.post(f"/api/pages/{p['id']}/poses", json={"action": " waving hand "})
    assert r.status_code == 202, r.text
    g = r.json()
    assert g["pose_mode"] == "precise"  # 排隊中（還沒有 workflow_name）也有正確的模式
    assert g["user_text"] == "waving hand"
    assert g["prompt"] == qwen_pose_prompt("waving hand")
    row = gen_row(app, g["id"])
    assert row.negative_prompt == ""
    rj = request_of(app, g["id"])
    assert rj["pose_mode"] == "precise" and "variation" in rj and rj["variation"] is None
    run_jobs(client, app)
    req = app.state.engine.requests[-1]
    assert req.negative_prompt == "" and (req.width, req.height) == POSE_SIZE


@pytest.mark.parametrize("mode", ["fast", "precise", "qwen", None])
def test_pose_ignores_mode(client, app, monkeypatch, mode):
    """舊前端送的 mode 一律忽略：只有精準（Qwen）一種。"""
    from app.api import compose as compose_api

    monkeypatch.setattr(compose_api, "pick_pose_variation", lambda rng: "facing left")
    b, _ = book_with_character(client, app)
    p = new_page(client, b["id"])
    r = client.post(f"/api/pages/{p['id']}/poses", json={"action": "揮手", "mode": mode})
    assert r.status_code == 202, r.text
    g = r.json()
    assert g["pose_mode"] == "precise"
    rj = request_of(app, g["id"])
    assert rj["pose_mode"] == "precise" and rj["variation"] == "facing left"
    assert g["prompt"] == qwen_pose_prompt("揮手", "facing left")
    assert gen_row(app, g["id"]).negative_prompt == ""


def test_pose_has_no_preflight_precheck(client, app):
    """快取的 preflight 說 Qwen 缺模型也照樣排隊（沒有 503）；缺東西時由生成失敗回報。"""
    b, _ = book_with_character(client, app)
    p = new_page(client, b["id"])
    app.state.preflight = qwen_missing_report("qwen_edit_pose_cutout")
    r = client.post(f"/api/pages/{p['id']}/poses", json={"action": "x"})
    assert r.status_code == 202, r.text
    assert r.json()["pose_mode"] == "precise"


def test_pose_records_variation(client, app, monkeypatch):
    from app.api import compose as compose_api

    monkeypatch.setattr(compose_api, "pick_pose_variation", lambda rng: "facing left")
    b, _ = book_with_character(client, app)
    p = new_page(client, b["id"])
    g = client.post(f"/api/pages/{p['id']}/poses", json={"action": "waving hand"}).json()
    assert g["prompt"] == qwen_pose_prompt("waving hand", "facing left")
    assert "facing left" in g["prompt"] and g["user_text"] == "waving hand"
    assert request_of(app, g["id"])["variation"] == "facing left"
    run_jobs(client, app)
    assert app.state.engine.requests[-1].variation == "facing left"


def test_pose_mode_out_for_old_rows_and_other_kinds(client, app):
    b, _ = book_with_character(client, app)
    p = new_page(client, b["id"])
    old = gen_pose(client, app, p["id"])
    with app.state.sessions() as s:  # 模擬 Phase 5 的舊列：request_json 沒有 pose_mode
        row = s.get(Generation, old)
        d = json.loads(row.request_json)
        d.pop("pose_mode")
        d.pop("variation")
        row.request_json = json.dumps(d)
        s.add(row)
        s.commit()
    assert client.get(f"/api/generations/{old}").json()["pose_mode"] == "fast"
    bg = gen_background(client, app, p["id"])
    assert client.get(f"/api/generations/{bg}").json()["pose_mode"] is None
    queued = gen_pose(client, app, p["id"], run=False)
    assert client.get(f"/api/generations/{queued}").json()["pose_mode"] == "precise"


def test_pose_mode_from_generation_tolerates_bad_request_json():
    from app.api.schemas import GenerationOut

    for rj in ("", "{oops", "[]", '{"pose_mode": "qwen"}'):
        g = Generation(id=1, book_id=1, page_id=1, kind=GenKind.POSE, user_text="", prompt="",
                       negative_prompt="", seed=0, width=8, height=8, request_json=rj)
        assert GenerationOut.from_generation(g).pose_mode == "fast", rj


def test_mask_endpoint_404s(client, app):
    b, p, bg, pose = compose_ready(client, app)
    assert client.get(f"/api/generations/{bg}/mask").status_code == 404
    assert client.get("/api/generations/999/mask").status_code == 404
    app.state.storage.delete(gen_row(app, pose).mask_path)
    assert client.get(f"/api/generations/{pose}/mask").status_code == 404


def test_cutout_alpha_and_size(client, app):
    b, p, bg, pose = compose_ready(client, app)
    g = client.get(f"/api/generations/{pose}").json()
    assert g["cutout_url"] == f"/api/generations/{pose}/cutout"
    assert client.get(f"/api/generations/{bg}").json()["cutout_url"] is None
    row = gen_row(app, pose)
    w, h = 40, 60
    mask = Image.new("L", (w, h), 0)
    mask.paste(255, (10, 20, 30, 50))
    buf = io.BytesIO()
    mask.save(buf, format="PNG")
    app.state.storage.put(row.mask_path, buf.getvalue())
    pbuf = io.BytesIO()
    Image.new("RGB", (w, h), (200, 10, 10)).save(pbuf, format="PNG")
    app.state.storage.put(row.image_path, pbuf.getvalue())
    r = client.get(g["cutout_url"])
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    im = Image.open(io.BytesIO(r.content))
    assert im.mode == "RGBA" and im.size == (20, 30)
    assert im.getpixel((10, 15))[3] == 255
    # 外接框內但遮罩為 0 的位置會是透明
    mask2 = Image.new("L", (w, h), 0)
    mask2.paste(255, (10, 20, 30, 25))
    mask2.paste(255, (10, 45, 30, 50))
    buf = io.BytesIO()
    mask2.save(buf, format="PNG")
    app.state.storage.put(row.mask_path, buf.getvalue())
    im = Image.open(io.BytesIO(client.get(g["cutout_url"]).content))
    assert im.size == (20, 30)
    assert im.getpixel((5, 12))[3] == 0 and im.getpixel((5, 2))[3] == 255


def test_cutout_404_and_409(client, app):
    b, p, bg, pose = compose_ready(client, app)
    assert client.get(f"/api/generations/{bg}/cutout").status_code == 404
    assert client.get("/api/generations/999/cutout").status_code == 404
    row = gen_row(app, pose)
    mask_bytes = app.state.storage.get(row.mask_path)
    app.state.storage.delete(row.mask_path)
    r = client.get(f"/api/generations/{pose}/cutout")
    # 物件不見了：和其他圖片端點一樣 404「圖片檔案遺失」
    assert r.status_code == 404 and r.json()["detail"] == "圖片檔案遺失"
    app.state.storage.put(row.mask_path, mask_bytes)
    image_bytes = app.state.storage.get(row.image_path)
    app.state.storage.delete(row.image_path)
    r = client.get(f"/api/generations/{pose}/cutout")
    assert r.status_code == 404 and r.json()["detail"] == "圖片檔案遺失"
    # 物件還在但壞掉（讀不出圖）：409，請使用者重新產生
    app.state.storage.put(row.image_path, image_bytes)
    app.state.storage.put(row.mask_path, b"not a png")
    r = client.get(f"/api/generations/{pose}/cutout")
    assert r.status_code == 409 and r.json()["detail"] == "圖片檔案遺失，請重新產生"


def test_pick_validation(client, app):
    b, p, bg, pose = compose_ready(client, app)
    pid = p["id"]
    # 種類不對
    assert pick(client, pid, "background", pose).status_code == 409
    assert pick(client, pid, "pose", bg).status_code == 409
    # 不存在
    assert pick(client, pid, "background", 999).status_code == 404
    assert pick(client, 999, "background", bg).status_code == 404
    # 別頁的
    other = new_page(client, b["id"])
    assert pick(client, other["id"], "background", bg).status_code == 409
    # 排隊中
    queued_bg = gen_background(client, app, pid, run=False)
    queued_pose = gen_pose(client, app, pid, run=False)
    assert pick(client, pid, "background", queued_bg).status_code == 409
    assert pick(client, pid, "pose", queued_pose).status_code == 409
    # 失敗
    run_jobs(client, app, 2)
    app.state.engine.outcomes.append(EngineError("boom"))
    failed = gen_pose(client, app, pid)
    assert gen_row(app, failed).status == GenStatus.FAILED
    assert pick(client, pid, "pose", failed).status_code == 409
    # 已捨棄
    assert client.patch(f"/api/generations/{queued_bg}", json={"discarded": True}).status_code == 200
    assert pick(client, pid, "background", queued_bg).status_code == 409
    # 角色動作沒有遮罩
    app.state.engine.pose_mask = False
    no_mask = gen_pose(client, app, pid)
    assert gen_row(app, no_mask).status == GenStatus.SUCCEEDED
    r = pick(client, pid, "pose", no_mask)
    assert r.status_code == 409
    # 頁面狀態沒被動到
    page = get_page(app, pid)
    assert (page.background_generation_id, page.pose_generation_id) == (bg, pose)


def test_in_use_background_and_pose_cannot_be_discarded(client, app):
    b, p, bg, pose = compose_ready(client, app)
    assert client.patch(f"/api/generations/{bg}", json={"discarded": True}).status_code == 409
    assert client.patch(f"/api/generations/{pose}", json={"discarded": True}).status_code == 409
    bg2 = gen_background(client, app, p["id"])
    picked(client, p["id"], "background", bg2)
    assert client.patch(f"/api/generations/{bg}", json={"discarded": True}).status_code == 200


# ---- 位置 ----------------------------------------------------------------------


def test_placement_roundtrip_and_validation(client, app):
    b, p, _ = legacy_page(client, app)
    make_passed(app, p["id"])
    url = f"/api/pages/{p['id']}/placement"
    body = {"cx": 0.25, "cy": 0.7, "height": 0.5, "flip": True}
    r = client.put(url, json=body)
    assert r.status_code == 200, r.text
    assert r.json()["placement"] == body
    assert r.json()["status"] == "passed"  # 位置草稿不改狀態
    for bad in ({**body, "cx": 1.5}, {**body, "cy": -0.1}, {**body, "height": 0.05},
                {**body, "height": 1.1}, {"cx": 0.5, "cy": 0.5}, {**body, "cx": "NaN"},
                {**body, "flip": "maybe"}):
        assert client.put(url, json=bad).status_code == 422, bad
    page = get_page(app, p["id"])
    assert json.loads(page.placement_json) == body and page.status == PageStatus.PASSED
    assert client.put("/api/pages/999/placement", json=body).status_code == 404


# ---- 確認合成 ------------------------------------------------------------------


def test_compose_creates_composite(client, app):
    b, p, bg, pose = compose_ready(client, app)
    page = confirm(client, p["id"])
    gid = page["selected_generation_id"]
    assert gid is not None and page["status"] == "draft" and page["character_check"] is None
    assert page["lineart_url"] and page["aspect_mismatch"] is False
    g = gen_row(app, gid)
    assert g.kind == GenKind.COMPOSITE and g.status == GenStatus.SUCCEEDED
    assert (g.engine, g.workflow_name, g.workflow_version) == ("compose", "compose", COMPOSE_VERSION)
    assert (g.seed, g.prompt, g.page_id) == (0, "", p["id"])
    assert json.loads(g.params_json) == {
        "background_generation_id": bg, "pose_generation_id": pose,
        "placement": DEFAULT_PLACEMENT.to_dict(),
    }
    with Image.open(io.BytesIO(app.state.storage.get(gen_row(app, bg).image_path))) as im:
        assert (g.width, g.height) == im.size
    with Image.open(io.BytesIO(app.state.storage.get(g.image_path))) as im:
        assert im.size == (g.width, g.height)
    assert client.get(f"/api/generations/{gid}/image").status_code == 200
    assert client.get(page["lineart_url"]).status_code == 200
    out = client.get(f"/api/generations/{gid}").json()
    assert out["kind"] == "composite" and out["mask_url"] is None


def test_compose_uses_page_placement(client, app):
    b, p, bg, pose = compose_ready(client, app)
    body = {"cx": 0.3, "cy": 0.5, "height": 0.4, "flip": True}
    client.put(f"/api/pages/{p['id']}/placement", json=body)
    gid = confirm(client, p["id"])["selected_generation_id"]
    assert json.loads(gen_row(app, gid).params_json)["placement"] == body


def test_compose_after_passed_demotes_to_draft(client, app):
    b, p, bg, pose = compose_ready(client, app)
    gid = confirm(client, p["id"])["selected_generation_id"]
    make_passed(app, p["id"])
    client.put(f"/api/pages/{p['id']}/placement", json={"cx": 0.4, "cy": 0.6, "height": 0.5, "flip": False})
    page = confirm(client, p["id"])
    assert page["selected_generation_id"] != gid
    assert page["status"] == "draft" and page["character_check"] is None
    pg = get_page(app, p["id"])
    assert pg.passed_at is None and pg.forced_pass_reason is None


def test_select_new_background_clears_composite(client, app):
    b, p, bg, pose = compose_ready(client, app)
    confirm(client, p["id"])
    make_passed(app, p["id"], forced_pass_reason="ok")
    # 選回同一張背景：不算換圖，什麼都不動
    page = picked(client, p["id"], "background", bg)
    assert page["selected_generation_id"] is not None and page["status"] == "passed"
    assert summary_reason(client, b["id"], p["id"]) is None
    bg2 = gen_background(client, app, p["id"])
    page = picked(client, p["id"], "background", bg2)
    assert page["background_generation_id"] == bg2
    assert page["selected_generation_id"] is None and page["status"] == "draft"
    assert page["character_check"] is None and page["lineart_url"] is None
    pg = get_page(app, p["id"])
    assert pg.passed_at is None and pg.forced_pass_reason is None
    assert summary_reason(client, b["id"], p["id"]) == "no_selection"
    assert client.get(f"/api/books/{b['id']}/export.pdf").status_code == 409
    assert client.get(f"/api/pages/{p['id']}/print.png").status_code == 409


def test_select_new_pose_clears_composite(client, app):
    b, p, bg, pose = compose_ready(client, app)
    confirm(client, p["id"])
    make_passed(app, p["id"])
    pose2 = gen_pose(client, app, p["id"], "sitting")
    page = picked(client, p["id"], "pose", pose2)
    assert page["pose_generation_id"] == pose2
    assert page["selected_generation_id"] is None and page["status"] == "draft"
    assert summary_reason(client, b["id"], p["id"]) == "no_selection"


def test_legacy_page_selection_survives_background_pick(client, app):
    b, p, ids = legacy_page(client, app)
    make_passed(app, p["id"], forced_pass_reason="ok")
    bg = gen_background(client, app, p["id"])
    page = picked(client, p["id"], "background", bg)
    assert page["selected_generation_id"] == ids[0] and page["status"] == "passed"
    assert page["character_check"] == "consistent" and page["lineart_url"]
    pose = gen_pose(client, app, p["id"])
    page = picked(client, p["id"], "pose", pose)
    assert page["selected_generation_id"] == ids[0] and page["status"] == "passed"
    pg = get_page(app, p["id"])
    assert pg.passed_at is not None and pg.forced_pass_reason == "ok"
    assert pg.character_check == CharacterCheck.CONSISTENT
    assert summary_reason(client, b["id"], p["id"]) is None
    # 確認合成才取代舊圖
    page = confirm(client, p["id"])
    assert page["selected_generation_id"] not in ids and page["status"] == "draft"


def test_compose_requires_ready_inputs(client, app):
    app.state.engine.image = color_png()
    b, _ = book_with_character(client, app, **A5_HOME)
    p = new_page(client, b["id"])
    pid = p["id"]

    def refused(msg):
        r = client.post(f"/api/pages/{pid}/compose")
        assert r.status_code == 409 and r.json()["detail"] == msg, r.text
        assert composites(app, pid) == []
        assert get_page(app, pid).selected_generation_id is None

    refused("請先選好背景")
    bg = gen_background(client, app, pid)
    picked(client, pid, "background", bg)
    refused("請先選好角色動作")
    # 角色動作還在排隊：不能選用，硬塞進頁面也不能合成
    queued = gen_pose(client, app, pid, run=False)
    assert pick(client, pid, "pose", queued).status_code == 409
    refused("請先選好角色動作")
    set_page(app, pid, pose_generation_id=queued)
    refused("請先選好角色動作")
    # 背景還在排隊（硬塞）也不行
    queued_bg = gen_background(client, app, pid, run=False)
    set_page(app, pid, background_generation_id=queued_bg)
    refused("請先選好背景")
    run_jobs(client, app, 2)
    set_page(app, pid, background_generation_id=bg, pose_generation_id=None)
    picked(client, pid, "pose", queued)
    # 遮罩檔被刪
    mask = gen_row(app, queued).mask_path
    mask_bytes = app.state.storage.get(mask)
    app.state.storage.delete(mask)
    refused("圖片檔案遺失，請重新產生")
    # 背景檔被刪
    app.state.storage.put(mask, mask_bytes)
    bg_path = gen_row(app, bg).image_path
    bg_bytes = app.state.storage.get(bg_path)
    app.state.storage.delete(bg_path)
    refused("圖片檔案遺失，請重新產生")
    app.state.storage.put(bg_path, bg_bytes)
    # 遮罩全黑（去背失敗）→ 409，不產生半成品
    buf = io.BytesIO()
    Image.new("L", POSE_SIZE, 0).save(buf, "PNG")
    app.state.storage.put(mask, buf.getvalue())
    r = client.post(f"/api/pages/{pid}/compose")
    assert r.status_code == 409 and "角色動作" in r.json()["detail"]
    assert composites(app, pid) == []
    assert not list(app.state.storage.root.rglob("*.tmp"))
    app.state.storage.put(mask, mask_bytes)
    assert confirm(client, pid)["selected_generation_id"] is not None
    assert client.post("/api/pages/999/compose").status_code == 404


def test_compose_reuses_identical_composite(client, app):
    b, p, bg, pose = compose_ready(client, app)
    pid = p["id"]
    gid = confirm(client, pid)["selected_generation_id"]
    r = client.put(f"/api/pages/{pid}/lineart/edits", json=edits_body(gid, [DRAW]))
    assert r.status_code == 200, r.text
    make_passed(app, pid)
    # 同背景＋動作＋位置再確認一次 → 同一張，修線與通過狀態都在
    page = confirm(client, pid)
    assert page["selected_generation_id"] == gid and page["status"] == "passed"
    assert len(client.get(f"/api/pages/{pid}/lineart/edits").json()["strokes"]) == 1
    # 位置只差在小數第 5 位 → 視為相同
    d = DEFAULT_PLACEMENT.to_dict()
    client.put(f"/api/pages/{pid}/placement", json={**d, "cx": d["cx"] + 0.00001})
    assert confirm(client, pid)["selected_generation_id"] == gid
    # 換背景（清空）再選回原背景 → 確認後選回原合成圖，修線回來
    bg2 = gen_background(client, app, pid)
    assert picked(client, pid, "background", bg2)["selected_generation_id"] is None
    picked(client, pid, "background", bg)
    page = confirm(client, pid)
    assert page["selected_generation_id"] == gid and page["status"] == "draft"
    edits = client.get(f"/api/pages/{pid}/lineart/edits").json()
    assert edits["generation_id"] == gid and len(edits["strokes"]) == 1
    assert len(composites(app, pid)) == 1


def test_compose_new_placement_new_composite(client, app):
    b, p, bg, pose = compose_ready(client, app)
    pid = p["id"]
    gid = confirm(client, pid)["selected_generation_id"]
    client.put(f"/api/pages/{pid}/lineart/edits", json=edits_body(gid, [DRAW]))
    client.put(f"/api/pages/{pid}/placement", json={"cx": 0.3, "cy": 0.6, "height": 0.5, "flip": True})
    gid2 = confirm(client, pid)["selected_generation_id"]
    assert gid2 != gid
    assert client.get(f"/api/pages/{pid}/lineart/edits").json()["strokes"] == []
    assert len(composites(app, pid)) == 2
    # 換動作也是新的合成圖
    pose2 = gen_pose(client, app, pid, "sitting")
    picked(client, pid, "pose", pose2)
    gid3 = confirm(client, pid)["selected_generation_id"]
    assert gid3 not in (gid, gid2)
    assert json.loads(gen_row(app, gid3).params_json)["pose_generation_id"] == pose2
    # 回到原動作＋預設位置 → 選回第一張
    picked(client, pid, "pose", pose)
    client.put(f"/api/pages/{pid}/placement", json=DEFAULT_PLACEMENT.to_dict())
    assert confirm(client, pid)["selected_generation_id"] == gid
    assert len(composites(app, pid)) == 3


def test_delete_page_removes_composite_files_and_masks(client, app):
    b, p, bg, pose = compose_ready(client, app)
    gid = confirm(client, p["id"])["selected_generation_id"]
    storage = app.state.storage
    keys = [gen_row(app, pose).mask_path, gen_row(app, gid).image_path, gen_row(app, bg).image_path]
    assert all(storage.exists(k) for k in keys)
    assert client.delete(f"/api/pages/{p['id']}").status_code == 204
    assert not any(storage.exists(k) for k in keys)


# ---- 待合成（compose_pending） --------------------------------------------------------


def page_json(client, book_id, page_id):
    return next(x for x in client.get(f"/api/books/{book_id}/pages").json() if x["id"] == page_id)


def test_compose_pending_tracks_selected_composite(client, app):
    app.state.engine.image = color_png()
    b, _ = book_with_character(client, app, **A5_HOME)
    p = new_page(client, b["id"], scene_prompt="公園")
    pid = p["id"]
    assert p["compose_pending"] is False
    bg = gen_background(client, app, pid)
    assert picked(client, pid, "background", bg)["compose_pending"] is False  # 還沒選角色動作
    pose = gen_pose(client, app, pid)
    assert picked(client, pid, "pose", pose)["compose_pending"] is True  # 還沒合成
    page = confirm(client, pid)
    assert page["compose_pending"] is False
    make_passed(app, pid)
    # 拖拉／翻轉後（位置草稿）→ 待重新合成，但不改通過狀態
    moved = {"cx": 0.3, "cy": 0.6, "height": 0.5, "flip": True}
    r = client.put(f"/api/pages/{pid}/placement", json=moved)
    assert r.status_code == 200 and r.json()["compose_pending"] is True
    assert r.json()["status"] == "passed"
    assert page_json(client, b["id"], pid)["compose_pending"] is True
    # 放回原位（小數第 5 位的差異視為相同）→ 不再待合成
    d = DEFAULT_PLACEMENT.to_dict()
    r = client.put(f"/api/pages/{pid}/placement", json={**d, "cx": d["cx"] + 0.00001})
    assert r.json()["compose_pending"] is False
    # 選定圖是另一個位置的合成圖（例如舊資料）→ 待合成
    client.put(f"/api/pages/{pid}/placement", json=moved)
    gid2 = confirm(client, pid)["selected_generation_id"]
    assert gid2 != page["selected_generation_id"]
    set_page(app, pid, selected_generation_id=page["selected_generation_id"])
    assert page_json(client, b["id"], pid)["compose_pending"] is True


def test_compose_pending_on_legacy_page(client, app):
    b, p, ids = legacy_page(client, app)
    pid = p["id"]
    assert p["compose_pending"] is False
    bg = gen_background(client, app, pid)
    assert picked(client, pid, "background", bg)["compose_pending"] is False  # 只有背景：仍是舊流程
    pose = gen_pose(client, app, pid)
    page = picked(client, pid, "pose", pose)
    assert page["selected_generation_id"] == ids[0] and page["compose_pending"] is True
    assert confirm(client, pid)["compose_pending"] is False


def test_compose_pending_when_composite_version_is_old(client, app):
    b, p, bg, pose = compose_ready(client, app)
    gid = confirm(client, p["id"])["selected_generation_id"]
    with app.state.sessions() as s:
        g = s.get(Generation, gid)
        g.workflow_version = "0"
        s.add(g)
        s.commit()
    assert page_json(client, b["id"], p["id"])["compose_pending"] is True


def test_compose_does_not_reuse_old_version_composite(client, app):
    b, p, bg, pose = compose_ready(client, app)
    pid = p["id"]
    gid = confirm(client, pid)["selected_generation_id"]
    with app.state.sessions() as s:
        g = s.get(Generation, gid)
        g.workflow_version = "0"
        s.add(g)
        s.commit()
    gid2 = confirm(client, pid)["selected_generation_id"]
    assert gid2 != gid
    assert gen_row(app, gid2).workflow_version == COMPOSE_VERSION
    assert len(composites(app, pid)) == 2


# ---- 確認合成時輸入被改（INPUTS_CHANGED） -------------------------------------------


def _files(storage):
    return set(storage.list("books/")) | set(storage.list("cache/"))


def test_compose_race_inputs_changed(client, app, monkeypatch):
    import app.api.compose as compose_api

    b, p, bg, pose = compose_ready(client, app)
    pid = p["id"]
    gid = confirm(client, pid)["selected_generation_id"]
    client.put(f"/api/pages/{pid}/placement", json={"cx": 0.3, "cy": 0.6, "height": 0.5, "flip": True})
    real = compose_api.compose
    raced = {"cx": 0.7, "cy": 0.5, "height": 0.4, "flip": False}

    def racing_compose(*args, **kwargs):
        # 合成進行中，另一個連線（例如另一個分頁）改了位置
        with app.state.sessions() as s:
            pg = s.get(compose_api.Page, pid)
            pg.placement_json = json.dumps(raced)
            s.add(pg)
            s.commit()
        return real(*args, **kwargs)

    monkeypatch.setattr(compose_api, "compose", racing_compose)
    root = app.state.storage
    before_files = _files(root)
    before_rows = {g.id for g in composites(app, pid)}
    r = client.post(f"/api/pages/{pid}/compose")
    assert r.status_code == 409
    assert r.json()["detail"] == "背景、角色動作或位置剛被更改，請重新確認合成"
    assert {g.id for g in composites(app, pid)} == before_rows  # 沒有留下新的合成圖
    assert _files(root) == before_files  # 寫出的合成圖檔已刪除
    pg = get_page(app, pid)
    assert pg.selected_generation_id == gid
    assert json.loads(pg.placement_json) == raced


def test_cutout_is_cacheable(client, app):
    b, p, bg, pose = compose_ready(client, app)
    r = client.get(f"/api/generations/{pose}/cutout")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "private, max-age=86400"


# ---- 批次背景（透過 materials/batch，只看背景部分）-----------------------------------


def batch(client, book_id):
    r = client.post(f"/api/books/{book_id}/materials/batch")
    assert r.status_code == 202, r.text
    out = r.json()
    return {
        "queued": [g for g in out["queued"] if g["kind"] == "background"],
        "skipped": [s for s in out["skipped"] if s["kind"] == "background"],
    }


def test_batch_backgrounds_skip_reasons_and_order(client, app):
    b = new_book(client)
    pages = [new_page(client, b["id"], scene_prompt=t) for t in ("  ", "有背景", "已選", "排隊中", "新的一頁", "另一頁")]
    ok_bg = gen_background(client, app, pages[1]["id"])
    picked_bg = gen_background(client, app, pages[2]["id"])
    picked(client, pages[2]["id"], "background", picked_bg)
    gen_background(client, app, pages[3]["id"], run=False)
    # 把順序反過來，確認依 position 走
    ids = [p["id"] for p in pages]
    client.post(f"/api/books/{b['id']}/pages/order", json={"page_ids": ids[::-1]})
    out = batch(client, b["id"])
    reasons = {s["page_id"]: s["reason"] for s in out["skipped"]}
    assert reasons == {
        ids[0]: "沒有場景描述",
        ids[1]: "已有背景",
        ids[2]: "已有背景",
        ids[3]: "背景產生中",
    }
    assert [s["position"] for s in out["skipped"]] == sorted(s["position"] for s in out["skipped"])
    assert [g["page_id"] for g in out["queued"]] == [ids[5], ids[4]]
    assert all(g["kind"] == "background" and g["status"] == "queued" for g in out["queued"])
    assert ok_bg


def test_batch_backgrounds_twice_second_is_all_in_progress(client, app):
    b = new_book(client)
    pages = [new_page(client, b["id"], scene_prompt=f"場景{i}") for i in range(3)]
    assert len(batch(client, b["id"])["queued"]) == 3
    out = batch(client, b["id"])
    assert out["queued"] == []
    assert {s["page_id"]: s["reason"] for s in out["skipped"]} == {
        p["id"]: "背景產生中" for p in pages
    }


def test_batch_backgrounds_discarded_only_background_is_queued(client, app):
    b = new_book(client)
    p = new_page(client, b["id"], scene_prompt="公園")
    bg = gen_background(client, app, p["id"])
    set_page(app, p["id"], background_generation_id=None)  # 自動選用的背景先取消選用才能捨棄
    assert client.patch(f"/api/generations/{bg}", json={"discarded": True}).status_code == 200
    out = batch(client, b["id"])
    assert [g["page_id"] for g in out["queued"]] == [p["id"]] and out["skipped"] == []


def test_batch_backgrounds_unknown_book(client):
    assert client.post("/api/books/999/materials/batch").status_code == 404


def test_batch_backgrounds_skips_legacy_page_selection(client, app):
    """舊流程：選用的是整頁候選圖（kind PAGE）→ 視為已有背景。"""
    b, p, ids = legacy_page(client, app, scene_prompt="公園")
    assert gen_row(app, ids[0]).kind == GenKind.PAGE
    out = batch(client, b["id"])
    assert out["queued"] == []
    assert out["skipped"] == [
        {"kind": "background", "page_id": p["id"], "position": p["position"], "reason": "已有背景"}
    ]


def _state(app, page_id):
    pg = get_page(app, page_id)
    return (pg.selected_generation_id, pg.status, pg.background_generation_id,
            pg.pose_generation_id, pg.passed_at)


def test_batch_backgrounds_leaves_page_state_alone(client, app):
    b, composed, _bg, _pose = compose_ready(client, app)
    composed = confirm(client, composed["id"])
    assert composed["selected_generation_id"] is not None
    # 舊流程頁面：選了整頁候選圖並已通過
    legacy = new_page(client, b["id"], scene_prompt="海邊")
    with app.state.sessions() as s:
        cand = Generation(book_id=b["id"], page_id=legacy["id"], kind=GenKind.PAGE, status=GenStatus.SUCCEEDED,
                          user_text="", prompt="p", negative_prompt="", seed=1, width=8, height=8,
                          request_json="{}", image_path="x.png")
        s.add(cand)
        s.commit()
        cand_id = cand.id
    set_page(app, legacy["id"], selected_generation_id=cand_id)
    make_passed(app, legacy["id"])
    # 會被排入的頁面：只選了角色動作
    fresh = new_page(client, b["id"], scene_prompt="森林")
    picked(client, fresh["id"], "pose", gen_pose(client, app, fresh["id"]))
    pids = [composed["id"], legacy["id"], fresh["id"]]
    before = {pid: _state(app, pid) for pid in pids}
    assert before[legacy["id"]][1] == PageStatus.PASSED
    assert before[fresh["id"]][3] is not None

    out = batch(client, b["id"])
    assert [g["page_id"] for g in out["queued"]] == [fresh["id"]]
    assert {s["page_id"]: s["reason"] for s in out["skipped"]} == {
        composed["id"]: "已有背景", legacy["id"]: "已有背景",
    }
    assert {pid: _state(app, pid) for pid in pids} == before


def test_batch_backgrounds_concurrent_calls_queue_once_per_page(client, app, monkeypatch):
    """兩個分頁同時按：後端序列化，每頁總共只排一張背景。"""
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor

    import app.api.compose as compose_api

    b = new_book(client)
    pages = [new_page(client, b["id"], scene_prompt=f"場景{i}") for i in range(3)]
    original = compose_api._last_simple_variation
    start = threading.Barrier(2)

    def slow_last(session, page_id):
        time.sleep(0.05)  # 放大競爭窗口：沒有鎖時兩個請求都會先看到「沒有背景」
        return original(session, page_id)

    monkeypatch.setattr(compose_api, "_last_simple_variation", slow_last)

    def call():
        start.wait()
        return batch(client, b["id"])

    with ThreadPoolExecutor(2) as ex:
        outs = list(ex.map(lambda _: call(), range(2)))
    queued = [g["page_id"] for o in outs for g in o["queued"]]
    assert sorted(queued) == sorted(p["id"] for p in pages)
    with app.state.sessions() as s:
        rows = s.exec(select(Generation).where(Generation.kind == GenKind.BACKGROUND)).all()
    assert sorted(g.page_id for g in rows) == sorted(p["id"] for p in pages)


# ---- 上傳背景 ------------------------------------------------------------------


def upload_bg(client, page_id, data, name="bg.png"):
    return client.post(f"/api/pages/{page_id}/backgrounds/upload",
                       files={"file": (name, data, "image/png")})


def png_bytes(size=(2000, 900), color="green"):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def test_upload_background_creates_selected_generation(client, app):
    b = new_book(client, **A5_HOME)
    p = new_page(client, b["id"])
    r = upload_bg(client, p["id"], png_bytes())
    assert r.status_code == 201, r.text
    page = r.json()
    gid = page["background_generation_id"]
    assert gid is not None
    g = gen_row(app, gid)
    assert (g.kind, g.status, g.engine) == (GenKind.BACKGROUND, GenStatus.SUCCEEDED, "upload")
    assert g.workflow_name is None and g.seed == 0
    assert (g.user_text, g.prompt, g.negative_prompt) == ("", "", "")
    params = json.loads(g.params_json)
    assert params["filename"] == "bg.png" and params["original_size"] == [2000, 900]
    assert len(params["crop_box"]) == 4
    assert json.loads(g.request_json)["source"] == "upload"
    with Image.open(io.BytesIO(app.state.storage.get(g.image_path))) as im:
        assert im.size == (g.width, g.height) == compute_layout(
            PaperSize(b["paper_size"]), PrintMode(b["print_mode"])).generation_size
    assert client.get(f"/api/generations/{gid}").json()["source"] == "upload"
    other = gen_background(client, app, p["id"])
    assert client.get(f"/api/generations/{other}").json()["source"] == "generated"


def test_upload_background_has_no_style_and_measures_time(client, app, monkeypatch):
    """上傳的背景沒有生成風格（不是「細緻」）；耗時記實際處理時間，不是 0。"""
    from types import SimpleNamespace

    import app.api.compose as compose_api

    ticks = iter([100.0, 100.75])
    monkeypatch.setattr(compose_api, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
    b = new_book(client, **A5_HOME)
    p = new_page(client, b["id"])
    gid = upload_bg(client, p["id"], png_bytes()).json()["background_generation_id"]
    out = client.get(f"/api/generations/{gid}").json()
    assert out["background_style"] is None
    assert out["elapsed_s"] == pytest.approx(0.75)


def test_upload_large_jpeg_decodes_reduced_and_encodes_once(client, app, monkeypatch):
    """大張 JPEG：用 draft 縮小解碼，且只編碼一次 PNG（最後存檔），不再整張轉 PNG 再解回來。"""
    b = new_book(client, **A5_HOME)
    p = new_page(client, b["id"])
    tw, th = compute_layout(PaperSize(b["paper_size"]), PrintMode(b["print_mode"])).generation_size
    big = (tw * 5, th * 5)
    buf = io.BytesIO()
    Image.new("RGB", big, "orange").save(buf, "JPEG", quality=80)
    saves: list[tuple[str, tuple[int, int]]] = []
    real_save = Image.Image.save

    def spy(self, fp, format=None, **kw):
        saves.append((str(format).upper(), self.size))
        return real_save(self, fp, format, **kw)

    monkeypatch.setattr(Image.Image, "save", spy)
    r = client.post(f"/api/pages/{p['id']}/backgrounds/upload",
                    files={"file": ("big.jpg", buf.getvalue(), "image/jpeg")})
    assert r.status_code == 201, r.text
    assert saves == [("PNG", (tw, th))]
    g = gen_row(app, r.json()["background_generation_id"])
    params = json.loads(g.params_json)
    assert params["original_size"] == list(big)
    l, t, rr, bb = params["crop_box"]
    assert (rr - l, bb - t) == (tw, th)
    with Image.open(io.BytesIO(app.state.storage.get(g.image_path))) as im:
        assert im.size == (tw, th)
        assert im.getpixel((tw // 2, th // 2))[0] > 200


def test_upload_rejects_bad_file(client, app):
    b = new_book(client)
    p = new_page(client, b["id"])
    r = upload_bg(client, p["id"], b"not an image")
    assert r.status_code == 400 and "無法解析" in r.json()["detail"]
    r = upload_bg(client, p["id"], b"x" * 11_000_000)
    assert r.status_code == 400
    assert get_page(app, p["id"]).background_generation_id is None
    assert not list(app.state.storage.root.rglob("generation*"))


def test_upload_16bit_grayscale_png_keeps_tones(client, app):
    """16-bit 灰階 PNG 上傳後不會變成幾乎全白（Pillow 直接轉 RGB 會截到 255）。"""
    import numpy as np

    b = new_book(client, **A5_HOME)
    p = new_page(client, b["id"])
    ramp = np.tile(np.linspace(0, 65535, 1024).round().astype(np.uint16), (1024, 1))
    src = Image.fromarray(ramp)
    assert src.mode == "I;16"
    buf = io.BytesIO()
    src.save(buf, "PNG")
    r = upload_bg(client, p["id"], buf.getvalue())
    assert r.status_code == 201, r.text
    with Image.open(io.BytesIO(app.state.storage.get(gen_row(app, r.json()["background_generation_id"]).image_path))) as im:
        out = im.convert("L")
        w, h = out.size
        mean = float(np.asarray(out, dtype=np.float64).mean())
        left, right = out.getpixel((2, h // 2)), out.getpixel((w - 3, h // 2))
    assert abs(mean - 127.5) < 8  # 置中裁切：左右對稱地切掉，平均不變
    assert left < 60 and right > 195


def test_upload_too_many_pixels_is_422_before_decoding(client, app, monkeypatch):
    """小檔案、超大尺寸（單色 PNG 壓得很小）：解碼前就擋（422），避免吃光記憶體。"""
    from PIL import ImageFile

    b = new_book(client)
    p = new_page(client, b["id"])
    buf = io.BytesIO()
    Image.new("1", (8001, 8000), 1).save(buf, "PNG")
    assert len(buf.getvalue()) < 1024 * 1024

    def no_decode(self):
        raise AssertionError("不應該解碼")

    monkeypatch.setattr(ImageFile.ImageFile, "load", no_decode)
    r = upload_bg(client, p["id"], buf.getvalue())
    assert r.status_code == 422 and r.json()["detail"] == "圖片像素太多（最多 6400 萬像素）"
    assert get_page(app, p["id"]).background_generation_id is None
    assert not list(app.state.storage.root.rglob("generation*"))


def test_upload_resets_composite_like_pick(client, app):
    b, p, bg, pose = compose_ready(client, app)
    confirm(client, p["id"])
    page = upload_bg(client, p["id"], png_bytes()).json()
    assert page["background_generation_id"] != bg
    assert page["selected_generation_id"] is None and page["status"] == "draft"


def test_upload_on_legacy_page_keeps_page_image(client, app):
    b, p, ids = legacy_page(client, app)
    page = upload_bg(client, p["id"], png_bytes()).json()
    assert page["selected_generation_id"] == ids[0]
    assert page["background_generation_id"] is not None


# ---- 產生全部素材 ----------------------------------------------------------------


def materials(client, book_id):
    r = client.post(f"/api/books/{book_id}/materials/batch")
    assert r.status_code == 202, r.text
    return r.json()


def test_materials_batch_counts(client, app):
    b, _ = book_with_character(client, app, **A5_HOME)
    p1 = new_page(client, b["id"], scene_prompt="公園", action_prompt="揮手")
    p2 = new_page(client, b["id"], scene_prompt="海邊")
    p3 = new_page(client, b["id"], scene_prompt="森林", action_prompt="跑步")
    bg = gen_background(client, app, p3["id"])
    picked(client, p3["id"], "background", bg)
    out = materials(client, b["id"])
    assert [(g["kind"], g["page_id"]) for g in out["queued"]] == [
        ("background", p1["id"]), ("pose", p1["id"]),
        ("background", p2["id"]), ("pose", p3["id"]),
    ]
    assert {(s["page_id"], s["kind"]): s["reason"] for s in out["skipped"]} == {
        (p2["id"], "pose"): "沒有動作描述",
        (p3["id"], "background"): "已有背景",
    }
    pose = next(g for g in out["queued"] if g["kind"] == "pose")
    assert gen_row(app, pose["id"]).user_text == "揮手"
    assert json.loads(gen_row(app, pose["id"]).request_json)["pose_mode"] == "precise"


def test_materials_batch_twice_no_duplicates(client, app):
    b, _ = book_with_character(client, app, **A5_HOME)
    p = new_page(client, b["id"], scene_prompt="公園", action_prompt="揮手")
    assert len(materials(client, b["id"])["queued"]) == 2
    out = materials(client, b["id"])
    assert out["queued"] == []
    assert {s["kind"]: s["reason"] for s in out["skipped"]} == {
        "background": "背景產生中", "pose": "角色動作產生中",
    }
    assert {s["page_id"] for s in out["skipped"]} == {p["id"]}


def test_materials_batch_needs_character(client, app):
    b = new_book(client)
    p = new_page(client, b["id"], scene_prompt="公園", action_prompt="揮手")
    out = materials(client, b["id"])
    assert [g["kind"] for g in out["queued"]] == ["background"]
    assert [(s["kind"], s["reason"]) for s in out["skipped"]] == [("pose", "請先設定角色")]
    assert out["skipped"][0]["page_id"] == p["id"]


def test_materials_batch_legacy_page_skips_both(client, app):
    b, _ = book_with_character(client, app, **A5_HOME)
    p = new_page(client, b["id"], scene_prompt="公園", action_prompt="揮手")
    with app.state.sessions() as s:
        g = Generation(book_id=b["id"], page_id=p["id"], kind=GenKind.PAGE, user_text="u",
                       prompt="p", negative_prompt="", seed=1, width=8, height=8,
                       request_json="", status=GenStatus.SUCCEEDED, image_path="x.png")
        s.add(g)
        s.commit()
        pg = s.get(Page, p["id"])
        pg.selected_generation_id = g.id
        s.add(pg)
        s.commit()
    out = materials(client, b["id"])
    assert out["queued"] == []
    assert {s["kind"]: s["reason"] for s in out["skipped"]} == {
        "background": "已有背景", "pose": "已有角色動作",
    }


def test_materials_batch_upload_page_ids_skip_background(client, app):
    b, _ = book_with_character(client, app, **A5_HOME)
    p1 = new_page(client, b["id"], scene_prompt="公園", action_prompt="揮手")
    p2 = new_page(client, b["id"], scene_prompt="海邊", action_prompt="跳")
    r = client.post(f"/api/books/{b['id']}/materials/batch", json={"upload_page_ids": [p1["id"]]})
    assert r.status_code == 202, r.text
    out = r.json()
    assert [(g["kind"], g["page_id"]) for g in out["queued"]] == [
        ("pose", p1["id"]), ("background", p2["id"]), ("pose", p2["id"]),
    ]
    assert out["skipped"] == [
        {"kind": "background", "page_id": p1["id"], "position": p1["position"], "reason": "改用上傳圖片"}
    ]
