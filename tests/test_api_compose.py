"""頁面合成流程 API：背景、角色動作、位置、確認合成。"""

import io
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlmodel import select

from app.config import load_settings
from app.main import create_app
from app.models import CharacterCheck, Generation, GenKind, GenStatus, PageStatus
from app.services.compose import COMPOSE_VERSION, DEFAULT_PLACEMENT
from app.services.engine import EngineError
from app.services.prompts import POSE_NEGATIVE, POSE_SIZE, pose_prompt, qwen_pose_prompt
from tests.fake_engine import FakeEngine, make_png
from tests.test_api_books import new_book, run_jobs
from tests.test_api_lineart import (
    A5_HOME, DRAW, color_png, edits_body, get_page, make_passed, selected_page, set_page,
)
from tests.test_api_pages import book_with_character, new_page, upload


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
    assert req.character_image is None and req.structure_image is None
    assert (req.width, req.height) == tuple(b["layout"]["generation_size"])
    page = picked(client, p["id"], "background", g["id"])
    assert page["background_generation_id"] == g["id"]
    assert page["pose_generation_id"] is None and page["placement"] is None
    assert page["selected_generation_id"] is None
    assert client.post("/api/pages/999/backgrounds").status_code == 404


def test_background_snapshots_sketch(client, app):
    p = new_page(client, new_book(client)["id"])
    upload(client, p["id"], make_png((128, 128)))
    client.patch(f"/api/pages/{p['id']}", json={"controlnet_strength": 0.3})
    gid = gen_background(client, app, p["id"])
    req = app.state.engine.requests[-1]
    assert req.structure_image is not None and req.structure_image.name == f"sketch_{gid}.png"
    assert req.structure_strength == 0.3
    # 之後刪掉草圖不影響已排入的快照
    client.delete(f"/api/pages/{p['id']}/sketch")
    assert req.structure_image.is_file()


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
    assert req.character_image == app.state.storage.abs(gen_row(app, char_gen).image_path)
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


def test_pose_mode_defaults_to_precise(client, app, monkeypatch):
    from app.api import compose as compose_api

    monkeypatch.setattr(compose_api, "pick_variation", lambda rng: None)
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
    assert req.pose_mode == "precise" and req.negative_prompt == ""
    assert (req.width, req.height) == POSE_SIZE


def test_pose_mode_fast_keeps_sdxl_prompt(client, app):
    b, _ = book_with_character(client, app)
    p = new_page(client, b["id"])
    r = client.post(f"/api/pages/{p['id']}/poses", json={"action": "waving hand", "mode": "fast"})
    assert r.status_code == 202, r.text
    g = r.json()
    assert g["pose_mode"] == "fast"
    row = gen_row(app, g["id"])
    assert row.prompt == pose_prompt(client.get(f"/api/books/{b['id']}").json()["character_description"], "waving hand")
    assert row.negative_prompt == POSE_NEGATIVE
    assert request_of(app, g["id"])["pose_mode"] == "fast"
    run_jobs(client, app)
    assert app.state.engine.requests[-1].pose_mode == "fast"


def test_pose_mode_invalid_is_422(client, app):
    b, _ = book_with_character(client, app)
    p = new_page(client, b["id"])
    for bad in ("qwen", "", None, 1):
        r = client.post(f"/api/pages/{p['id']}/poses", json={"action": "x", "mode": bad})
        assert r.status_code == 422, bad


def _qwen_missing_gguf_report():
    from app.services.preflight import MissingModel, PreflightReport, WorkflowReport

    return PreflightReport(ok=False, comfy_url="http://x", comfy_reachable=True, workflows=[
        WorkflowReport(name="qwen_edit_pose_cutout", ok=False, version="1", missing_models=[
            MissingModel("qwen_edit_2511_q4km", "qwen-image-edit-2511-Q4_K_M.gguf", "unet", "沒有此檔案")]),
        WorkflowReport(name="sdxl_pose_cutout", ok=True, version="1"),
    ])


def test_precise_unavailable_is_503_and_fast_still_works(client, app):
    b, _ = book_with_character(client, app)
    p = new_page(client, b["id"])
    app.state.preflight = _qwen_missing_gguf_report()
    for body in ({"action": "x", "mode": "precise"}, {"action": "x"}):  # 沒送 mode 也一樣（不偷偷改成快速）
        r = client.post(f"/api/pages/{p['id']}/poses", json=body)
        assert r.status_code == 503, r.text
        detail = r.json()["detail"]
        assert detail.startswith("精準模式缺少：")
        assert "qwen-image-edit-2511-Q4_K_M.gguf" in detail
    with app.state.sessions() as s:  # 503 不會留下排隊中的 generation
        assert not s.exec(select(Generation).where(Generation.kind == GenKind.POSE)).all()
    r = client.post(f"/api/pages/{p['id']}/poses", json={"action": "x", "mode": "fast"})
    assert r.status_code == 202, r.text
    assert r.json()["pose_mode"] == "fast"


def test_precise_pose_records_variation(client, app, monkeypatch):
    from app.api import compose as compose_api

    monkeypatch.setattr(compose_api, "pick_variation", lambda rng: "facing left")
    b, _ = book_with_character(client, app)
    p = new_page(client, b["id"])
    g = client.post(f"/api/pages/{p['id']}/poses", json={"action": "waving hand"}).json()
    assert g["prompt"] == qwen_pose_prompt("waving hand", "facing left")
    assert "facing left" in g["prompt"] and g["user_text"] == "waving hand"
    assert request_of(app, g["id"])["variation"] == "facing left"
    run_jobs(client, app)
    assert app.state.engine.requests[-1].variation == "facing left"


def test_fast_pose_does_not_pick_variation(client, app, monkeypatch):
    from app.api import compose as compose_api

    monkeypatch.setattr(compose_api, "pick_variation", lambda rng: "facing left")
    b, _ = book_with_character(client, app)
    p = new_page(client, b["id"])
    g = client.post(f"/api/pages/{p['id']}/poses", json={"action": "waving hand", "mode": "fast"}).json()
    assert "facing left" not in g["prompt"]
    assert request_of(app, g["id"])["variation"] is None


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
    app.state.storage.write(row.mask_path, buf.getvalue())
    pbuf = io.BytesIO()
    Image.new("RGB", (w, h), (200, 10, 10)).save(pbuf, format="PNG")
    app.state.storage.write(row.image_path, pbuf.getvalue())
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
    app.state.storage.write(row.mask_path, buf.getvalue())
    im = Image.open(io.BytesIO(client.get(g["cutout_url"]).content))
    assert im.size == (20, 30)
    assert im.getpixel((5, 12))[3] == 0 and im.getpixel((5, 2))[3] == 255


def test_cutout_404_and_409(client, app):
    b, p, bg, pose = compose_ready(client, app)
    assert client.get(f"/api/generations/{bg}/cutout").status_code == 404
    assert client.get("/api/generations/999/cutout").status_code == 404
    row = gen_row(app, pose)
    mask_bytes = app.state.storage.abs(row.mask_path).read_bytes()
    app.state.storage.delete(row.mask_path)
    r = client.get(f"/api/generations/{pose}/cutout")
    assert r.status_code == 409 and r.json()["detail"] == "圖片檔案遺失，請重新產生"
    app.state.storage.write(row.mask_path, mask_bytes)
    app.state.storage.delete(row.image_path)
    assert client.get(f"/api/generations/{pose}/cutout").status_code == 409


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
    b, p, _ = selected_page(client, app)
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
    with Image.open(app.state.storage.abs(gen_row(app, bg).image_path)) as im:
        assert (g.width, g.height) == im.size
    with Image.open(app.state.storage.abs(g.image_path)) as im:
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
    b, p, ids = selected_page(client, app)
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
    mask_bytes = app.state.storage.abs(mask).read_bytes()
    app.state.storage.delete(mask)
    refused("圖片檔案遺失，請重新產生")
    # 背景檔被刪
    app.state.storage.write(mask, mask_bytes)
    bg_path = gen_row(app, bg).image_path
    bg_bytes = app.state.storage.abs(bg_path).read_bytes()
    app.state.storage.delete(bg_path)
    refused("圖片檔案遺失，請重新產生")
    app.state.storage.write(bg_path, bg_bytes)
    # 遮罩全黑（去背失敗）→ 409，不產生半成品
    buf = io.BytesIO()
    Image.new("L", POSE_SIZE, 0).save(buf, "PNG")
    app.state.storage.write(mask, buf.getvalue())
    r = client.post(f"/api/pages/{pid}/compose")
    assert r.status_code == 409 and "角色動作" in r.json()["detail"]
    assert composites(app, pid) == []
    assert not list(app.state.storage.root.rglob("*.tmp"))
    app.state.storage.write(mask, mask_bytes)
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


def test_select_generation_accepts_composite(client, app):
    b, p, bg, pose = compose_ready(client, app)
    pid = p["id"]
    gid = confirm(client, pid)["selected_generation_id"]
    client.put(f"/api/pages/{pid}/placement", json={"cx": 0.3, "cy": 0.6, "height": 0.5, "flip": True})
    gid2 = confirm(client, pid)["selected_generation_id"]
    r = client.post(f"/api/pages/{pid}/select", json={"generation_id": gid})
    assert r.status_code == 200 and r.json()["selected_generation_id"] == gid
    for wrong in (bg, pose):
        assert client.post(f"/api/pages/{pid}/select", json={"generation_id": wrong}).status_code == 409
    assert gid2


def test_delete_page_removes_composite_files_and_masks(client, app):
    b, p, bg, pose = compose_ready(client, app)
    gid = confirm(client, p["id"])["selected_generation_id"]
    storage = app.state.storage
    files = [storage.abs(gen_row(app, pose).mask_path), storage.abs(gen_row(app, gid).image_path),
             storage.abs(gen_row(app, bg).image_path)]
    assert all(f.is_file() for f in files)
    assert client.delete(f"/api/pages/{p['id']}").status_code == 204
    assert not any(f.exists() for f in files)


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
    # 用 /select 選回某張合成圖，位置不同 → 待合成
    client.put(f"/api/pages/{pid}/placement", json=moved)
    gid2 = confirm(client, pid)["selected_generation_id"]
    assert gid2 != page["selected_generation_id"]
    r = client.post(f"/api/pages/{pid}/select", json={"generation_id": page["selected_generation_id"]})
    assert r.status_code == 200 and r.json()["compose_pending"] is True


def test_compose_pending_on_legacy_page(client, app):
    b, p, ids = selected_page(client, app)
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


def _files(root):
    return {f for f in root.rglob("*.png") if f.is_file()}


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
    root = app.state.storage.abs(".")
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
