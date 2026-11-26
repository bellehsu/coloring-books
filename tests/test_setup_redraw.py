"""設定頁改既有頁面（fix round 2026-11-13 #7）：改場景／動作標「要重畫」，「繼續做」（/produce）重畫它們。

- `PATCH /api/pages/{id}?from_setup=true`：場景變了、頁面有 AI 背景 → redraw_background；動作變了、
  頁面有角色動作 → redraw_pose。上傳的背景只存文字；圖下方的字不標；C 的 oneclick 與一般 PATCH 不標。
- /produce 把標了的頁面照空格一樣排（額度內），排到時清掉旗標；角色動作是 take＋review（review_pose），
  背景是 take、沒有 review。放不下（429）的旗標留著。
- overview：redraw_count 是標了、而且真的畫得出來（有文字；動作還要有主角）的頁數，不算進 deferred_count（「明天繼續」）。
"""

import json

import pytest
from fastapi.testclient import TestClient

from app.config import load_settings
from app.main import create_app
from app.models import Book, Generation, GenKind, GenStatus
from app.services import usage
from app.services.page_state import ATTENTION_REVIEW_POSE
from tests.fake_engine import FakeEngine
from tests.test_api_compose import gen_pose, picked, upload_bg
from tests.test_api_lineart import color_png, get_page
from tests.test_api_produce import THREE, book_with_pages, overview, plan, produce, run_all
from tests.test_usage import NOW, login, set_cap, used

CAP_MSG = "今天的生成次數已用完，明天再來"


@pytest.fixture
def dev_owner():
    return None  # 用真正的邀請連結：額度算在這個 owner 身上


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    monkeypatch.setattr(usage, "clock", lambda: NOW)


@pytest.fixture
def app(settings_file):
    return create_app(load_settings(settings_file), engine=FakeEngine(), start_worker=False)


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


# ---- 小工具 ------------------------------------------------------------------------


def ready_book(client, app, pages=THREE, cap=60):
    """建書＋角色＋頁面，全部做好（背景、角色動作、頁面流程）。回傳 (owner, book, [page])。"""
    owner = login(client, app, cap=cap)
    b, ps = book_with_pages(client, app, pages)
    produce(client, b["id"])
    run_all(client, app)
    for p in ps:
        page = get_page(app, p["id"])
        assert page.background_generation_id is not None and page.pose_generation_id is not None
    return owner, b, ps


def patch(client, page_id, body, **params):
    r = client.patch(f"/api/pages/{page_id}", params=params, json=body)
    assert r.status_code == 200, r.text
    return r.json()


def setup_edit(client, page_id, **body):
    return patch(client, page_id, body, from_setup="true")


def flags(app, page_id) -> tuple[bool, bool]:
    page = get_page(app, page_id)
    return page.redraw_background, page.redraw_pose


def slots(app, page_id) -> tuple[int | None, int | None, int | None]:
    page = get_page(app, page_id)
    return page.background_generation_id, page.pose_generation_id, page.selected_generation_id


def page_json(client, book_id, page_id) -> dict:
    return next(p for p in client.get(f"/api/books/{book_id}/pages").json() if p["id"] == page_id)


def rj(app, gen_id) -> dict:
    with app.state.sessions() as s:
        return json.loads(s.get(Generation, gen_id).request_json)


# ---- 設定頁的 PATCH -------------------------------------------------------------------


def test_setup_scene_edit_flags_background(client, app):
    _, _, (p1, p2, _) = ready_book(client, app)
    out = setup_edit(client, p1["id"], scene_prompt="雪地")
    assert out["scene_prompt"] == "雪地"
    assert out["redraw_background"] is True and out["redraw_pose"] is False
    assert flags(app, p1["id"]) == (True, False)
    assert flags(app, p2["id"]) == (False, False)
    # 同樣的場景再存一次：沒變，不另外標（旗標也不會被清掉）
    setup_edit(client, p2["id"], scene_prompt="海邊")
    assert flags(app, p2["id"]) == (False, False)


def test_setup_action_edit_flags_pose(client, app):
    _, _, (p1, _, _) = ready_book(client, app)
    out = setup_edit(client, p1["id"], action_prompt="坐著")
    assert out["action_prompt"] == "坐著"
    assert out["redraw_background"] is False and out["redraw_pose"] is True
    assert flags(app, p1["id"]) == (False, True)


def test_setup_edit_on_page_without_art_sets_no_flag(client, app):
    login(client, app)
    _, (p1,) = book_with_pages(client, app, [("公園", "揮手")])
    out = setup_edit(client, p1["id"], scene_prompt="雪地", action_prompt="坐著")
    assert out["scene_prompt"] == "雪地" and out["action_prompt"] == "坐著"
    assert flags(app, p1["id"]) == (False, False)


def test_setup_scene_edit_on_uploaded_background_saves_text_only(client, app):
    login(client, app)
    _, (p1,) = book_with_pages(client, app, [("公園", "揮手")])
    assert upload_bg(client, p1["id"], color_png()).status_code == 201
    assert get_page(app, p1["id"]).background_generation_id is not None
    out = setup_edit(client, p1["id"], scene_prompt="雪地")
    assert out["scene_prompt"] == "雪地"
    assert flags(app, p1["id"]) == (False, False)


def test_setup_caption_edit_sets_no_flag(client, app):
    _, _, (p1, _, _) = ready_book(client, app)
    out = setup_edit(client, p1["id"], caption_zh="新的字", caption_en="New words")
    assert out["caption_zh"] == "新的字" and out["caption_en"] == "New words"
    assert flags(app, p1["id"]) == (False, False)


def test_setup_blank_edit_sets_no_flag(client, app):
    """場景／動作改成空白：沒有東西可以畫，不標（文字照存）。"""
    _, _, (p1, p2, _) = ready_book(client, app)
    out = setup_edit(client, p1["id"], scene_prompt="   ", action_prompt="")
    assert out["scene_prompt"] == "" and out["action_prompt"] == ""
    setup_edit(client, p2["id"], action_prompt="  ")
    assert flags(app, p1["id"]) == (False, False)
    assert flags(app, p2["id"]) == (False, False)


def test_setup_with_oneclick_is_400(client, app):
    _, _, (p1, _, _) = ready_book(client, app)
    r = client.patch(f"/api/pages/{p1['id']}", params={"from_setup": "true", "oneclick": "true"},
                     json={"action_prompt": "坐著"})
    assert r.status_code == 400 and r.json()["detail"] == "參數衝突"
    page = get_page(app, p1["id"])
    assert page.action_prompt == "揮手" and (page.redraw_background, page.redraw_pose) == (False, False)


def test_c_and_plain_patch_never_flag(client, app):
    _, _, (p1, p2, _) = ready_book(client, app)
    patch(client, p1["id"], {"action_prompt": "坐著", "scene_prompt": "雪地"}, oneclick="true")
    patch(client, p2["id"], {"action_prompt": "坐著", "scene_prompt": "雪地"})
    assert flags(app, p1["id"]) == (False, False)
    assert flags(app, p2["id"]) == (False, False)


# ---- /produce 重畫標了的頁 -------------------------------------------------------------


def test_produce_redraws_only_flagged_pages(client, app):
    _, b, (p1, p2, p3) = ready_book(client, app)
    before = {p["id"]: slots(app, p["id"]) for p in (p1, p2, p3)}
    setup_edit(client, p2["id"], action_prompt="坐著")
    got = plan(client, b["id"])
    assert got["poses"] == 1 and got["backgrounds"] == 0 and got["total"] == 1

    out = produce(client, b["id"])
    assert len(out["queued"]) == 1 and out["deferred_pages"] == [] and out["pipeline_pages"] == []
    gid = out["queued"][0]
    with app.state.sessions() as s:
        g = s.get(Generation, gid)
        assert (g.page_id, g.kind, g.status, g.user_text) == (p2["id"], GenKind.POSE, GenStatus.QUEUED, "坐著")
    d = rj(app, gid)
    assert d["take"] is True and d["review"] is True
    assert flags(app, p2["id"]) == (False, False)  # 排到了：旗標清掉
    assert produce(client, b["id"])["queued"] == []  # 再按一次不重複

    run_all(client, app)
    page2 = get_page(app, p2["id"])
    assert flags(app, p2["id"]) == (False, False)  # 換上去之後也是
    assert page_json(client, b["id"], p2["id"])["can_undo"] is True  # take 存了復原快照
    assert page2.pose_generation_id == gid != before[p2["id"]][1]
    assert page2.background_generation_id == before[p2["id"]][0]
    assert page2.attention == ATTENTION_REVIEW_POSE
    assert slots(app, p1["id"]) == before[p1["id"]]
    assert slots(app, p3["id"]) == before[p3["id"]]


def test_produce_flagged_background_is_take_without_review(client, app):
    _, b, (p1, _) = ready_book(client, app, pages=[("公園", "揮手"), ("海邊", "跳")])
    old_bg, old_pose, _ = slots(app, p1["id"])
    setup_edit(client, p1["id"], scene_prompt="雪地")
    got = plan(client, b["id"])
    assert got["backgrounds"] == 1 and got["poses"] == 0

    out = produce(client, b["id"])
    assert len(out["queued"]) == 1
    gid = out["queued"][0]
    with app.state.sessions() as s:
        g = s.get(Generation, gid)
        assert (g.page_id, g.kind, g.user_text) == (p1["id"], GenKind.BACKGROUND, "雪地")
    d = rj(app, gid)
    assert d["take"] is True and "review" not in d
    assert flags(app, p1["id"]) == (False, False)

    run_all(client, app)
    page = get_page(app, p1["id"])
    assert flags(app, p1["id"]) == (False, False)
    assert page_json(client, b["id"], p1["id"])["can_undo"] is True
    assert page.background_generation_id == gid != old_bg
    assert page.pose_generation_id == old_pose
    assert page.attention is None


def test_overview_redraw_count_not_deferred(client, app):
    _, b, (p1, _, _) = ready_book(client, app)
    ov = overview(client, b["id"])
    assert ov["redraw_count"] == 0 and ov["deferred_count"] == 0
    setup_edit(client, p1["id"], scene_prompt="雪地", action_prompt="坐著")  # 一頁兩種都標：仍算 1 頁
    ov = overview(client, b["id"])
    assert ov["redraw_count"] == 1 and ov["deferred_count"] == 0
    pages = client.get(f"/api/books/{b['id']}/pages").json()
    assert [(p["redraw_background"], p["redraw_pose"]) for p in pages] == [
        (True, True), (False, False), (False, False)]
    produce(client, b["id"])
    assert overview(client, b["id"])["redraw_count"] == 0


def test_flag_kept_when_cap_exhausted(client, app):
    owner, b, (p1, _, _) = ready_book(client, app)
    set_cap(app, owner, used(app, owner))  # 今天剩 0 次
    setup_edit(client, p1["id"], action_prompt="坐著")
    assert plan(client, b["id"])["remaining"] == 0
    r = client.post(f"/api/books/{b['id']}/produce")
    assert r.status_code == 429 and r.json()["detail"] == CAP_MSG
    assert flags(app, p1["id"]) == (False, True)


def test_flag_kept_while_same_kind_is_in_progress(client, app):
    """這頁同種素材還在排隊／執行：不另外排，旗標留著等下一次。"""
    _, b, (p1, _, _) = ready_book(client, app)
    setup_edit(client, p1["id"], action_prompt="坐著")
    assert len(produce(client, b["id"])["queued"]) == 1  # 重畫的角色動作在排隊
    setup_edit(client, p1["id"], action_prompt="跑")  # 又改了一次
    assert flags(app, p1["id"]) == (False, True)
    assert plan(client, b["id"])["total"] == 0
    assert produce(client, b["id"])["queued"] == []
    assert flags(app, p1["id"]) == (False, True)


# ---- 新的選擇清掉旗標（復原不算） --------------------------------------------------------


def test_upload_after_flag_clears_background_flag(client, app):
    _, b, (p1, _, _) = ready_book(client, app)
    setup_edit(client, p1["id"], scene_prompt="雪地")
    r = upload_bg(client, p1["id"], color_png())
    assert r.status_code == 201, r.text
    uploaded = get_page(app, p1["id"]).background_generation_id
    assert flags(app, p1["id"]) == (False, False)
    assert plan(client, b["id"])["backgrounds"] == 0
    assert produce(client, b["id"])["queued"] == []
    assert get_page(app, p1["id"]).background_generation_id == uploaded  # 上傳的留著


def test_pick_after_flag_clears_pose_flag(client, app):
    _, b, (p1, _, _) = ready_book(client, app)
    first = get_page(app, p1["id"]).pose_generation_id
    second = gen_pose(client, app, p1["id"])
    assert get_page(app, p1["id"]).pose_generation_id == second
    setup_edit(client, p1["id"], action_prompt="坐著")
    assert flags(app, p1["id"]) == (False, True)
    picked(client, p1["id"], "pose", first)  # 自己選回第一張
    assert flags(app, p1["id"]) == (False, False)
    assert plan(client, b["id"])["poses"] == 0


def test_new_auto_selected_pose_clears_flag(client, app):
    """新的 AI 角色動作好了、自動選上去：也是新的選擇。"""
    _, b, (p1, _, _) = ready_book(client, app)
    setup_edit(client, p1["id"], action_prompt="坐著")
    new = gen_pose(client, app, p1["id"], action="坐著")
    assert get_page(app, p1["id"]).pose_generation_id == new
    assert flags(app, p1["id"]) == (False, False)
    assert plan(client, b["id"])["poses"] == 0


def test_undo_restoring_an_older_pose_keeps_flag(client, app):
    _, _, (p1, _, _) = ready_book(client, app)
    first = get_page(app, p1["id"]).pose_generation_id
    second = gen_pose(client, app, p1["id"])  # 自動換上去（存了復原快照）
    assert get_page(app, p1["id"]).pose_generation_id == second
    setup_edit(client, p1["id"], action_prompt="坐著")
    r = client.post(f"/api/pages/{p1['id']}/undo")
    assert r.status_code == 200, r.text
    assert get_page(app, p1["id"]).pose_generation_id == first
    assert flags(app, p1["id"]) == (False, True)  # 復原不是新的選擇


def test_partial_grant_clears_only_queued_flags(client, app):
    owner, b, (p1, p2, _) = ready_book(client, app)
    setup_edit(client, p1["id"], action_prompt="坐著")
    setup_edit(client, p2["id"], action_prompt="坐著")
    set_cap(app, owner, used(app, owner) + 1)  # 今天剩 1 次
    out = produce(client, b["id"])
    assert len(out["queued"]) == 1 and out["deferred_pages"] == [p2["id"]]
    with app.state.sessions() as s:
        assert s.get(Generation, out["queued"][0]).page_id == p1["id"]
    assert flags(app, p1["id"]) == (False, False)
    assert flags(app, p2["id"]) == (False, True)


def test_materials_batch_leaves_flagged_page_alone(client, app):
    _, b, (p1, _, _) = ready_book(client, app)
    before = slots(app, p1["id"])
    setup_edit(client, p1["id"], scene_prompt="雪地", action_prompt="坐著")
    r = client.post(f"/api/books/{b['id']}/materials/batch")
    assert r.status_code == 202, r.text
    assert r.json()["queued"] == []
    assert flags(app, p1["id"]) == (True, True)
    assert slots(app, p1["id"]) == before


# ---- 最終審查修正：舊的 take 不清新的旗標、卡住的旗標、復原清掉 review_pose ----------------------


def test_older_take_landing_keeps_newer_setup_flag(client, app):
    """「繼續做」排了「坐著」，之後設定頁又改成「跑」：舊的 take 換上去不能清掉新的「要重畫」。"""
    _, b, (p1, _, _) = ready_book(client, app)
    setup_edit(client, p1["id"], action_prompt="坐著")
    first = produce(client, b["id"])["queued"]
    assert len(first) == 1 and flags(app, p1["id"]) == (False, False)
    setup_edit(client, p1["id"], action_prompt="跑")  # 排隊中又改了
    assert flags(app, p1["id"]) == (False, True)
    run_all(client, app)  # 「坐著」的 take 換上去
    assert get_page(app, p1["id"]).pose_generation_id == first[0]
    assert flags(app, p1["id"]) == (False, True)  # 還是要重畫成「跑」
    again = produce(client, b["id"])["queued"]
    assert len(again) == 1
    with app.state.sessions() as s:
        assert s.get(Generation, again[0]).user_text == "跑"
    assert flags(app, p1["id"]) == (False, False)


def test_take_with_current_text_clears_flag(client, app):
    """take 的文字等於目前的動作：使用者要的就是它，旗標清掉。"""
    _, b, (p1, _, _) = ready_book(client, app)
    setup_edit(client, p1["id"], action_prompt="坐著")
    assert len(produce(client, b["id"])["queued"]) == 1
    setup_edit(client, p1["id"], action_prompt="跑")
    setup_edit(client, p1["id"], action_prompt="坐著")  # 又改回來
    assert flags(app, p1["id"]) == (False, True)
    run_all(client, app)
    assert flags(app, p1["id"]) == (False, False)


def test_older_auto_selected_pose_keeps_newer_flag(client, app):
    """自動選上去的新角色動作文字跟目前的動作不同：旗標留著。"""
    _, b, (p1, _, _) = ready_book(client, app)
    setup_edit(client, p1["id"], action_prompt="坐著")
    new = gen_pose(client, app, p1["id"], action="waving hand")
    assert get_page(app, p1["id"]).pose_generation_id == new
    assert flags(app, p1["id"]) == (False, True)


def test_setup_edit_emptying_text_clears_flag(client, app):
    _, b, (p1, _, _) = ready_book(client, app)
    setup_edit(client, p1["id"], scene_prompt="雪地", action_prompt="坐著")
    assert flags(app, p1["id"]) == (True, True)
    setup_edit(client, p1["id"], action_prompt="")
    assert flags(app, p1["id"]) == (True, False)
    setup_edit(client, p1["id"], scene_prompt="  ")
    assert flags(app, p1["id"]) == (False, False)
    assert overview(client, b["id"])["redraw_count"] == 0


def test_redraw_count_skips_blank_text(client, app):
    """C 把動作清空（oneclick 的 PATCH 不動旗標）：沒有東西可以畫，不算「要重畫」。"""
    _, b, (p1, p2, _) = ready_book(client, app)
    setup_edit(client, p1["id"], action_prompt="坐著")
    setup_edit(client, p2["id"], scene_prompt="雪地")
    assert overview(client, b["id"])["redraw_count"] == 2
    patch(client, p1["id"], {"action_prompt": ""}, oneclick="true")
    assert flags(app, p1["id"]) == (False, True)
    patch(client, p2["id"], {"scene_prompt": ""})
    assert flags(app, p2["id"]) == (True, False)
    ov = overview(client, b["id"])
    assert ov["redraw_count"] == 0
    assert plan(client, b["id"])["total"] == 0


def test_redraw_count_pose_needs_character(client, app):
    _, b, (p1, _, _) = ready_book(client, app)
    setup_edit(client, p1["id"], action_prompt="坐著")
    assert overview(client, b["id"])["redraw_count"] == 1
    with app.state.sessions() as s:
        book = s.get(Book, b["id"])
        book.character_generation_id = None
        s.add(book)
        s.commit()
    assert overview(client, b["id"])["redraw_count"] == 0


def test_undo_of_review_take_clears_review_pose(client, app):
    """批次重畫的 take 換上去標了 review_pose；復原選回舊的動作就不用再看了，重做也一樣不再標。"""
    _, b, (p1, _, _) = ready_book(client, app)
    old_pose = get_page(app, p1["id"]).pose_generation_id
    setup_edit(client, p1["id"], action_prompt="坐著")
    produce(client, b["id"])
    run_all(client, app)
    assert get_page(app, p1["id"]).attention == ATTENTION_REVIEW_POSE
    r = client.post(f"/api/pages/{p1['id']}/undo")
    assert r.status_code == 200, r.text
    page = get_page(app, p1["id"])
    assert page.pose_generation_id == old_pose
    assert (page.attention, page.attention_detail) == (None, None)
    r = client.post(f"/api/pages/{p1['id']}/redo")
    assert r.status_code == 200, r.text
    assert get_page(app, p1["id"]).attention is None


def test_undo_without_pose_change_keeps_review_pose(client, app):
    """復原只改了文字（動作沒換）：review_pose 留著。"""
    _, b, (p1, _, _) = ready_book(client, app)
    setup_edit(client, p1["id"], action_prompt="坐著")
    produce(client, b["id"])
    run_all(client, app)
    patch(client, p1["id"], {"caption_zh": "改字"}, oneclick="true")
    r = client.post(f"/api/pages/{p1['id']}/undo")
    assert r.status_code == 200, r.text
    assert get_page(app, p1["id"]).attention == ATTENTION_REVIEW_POSE


def test_restore_auto_pose_clears_review_pose_background_restore_keeps_it(client, app):
    """「還原成自動畫的那張」換了角色動作是使用者自己的選擇：清掉 review_pose；還原背景不動它。"""
    _, b, (p1, _, _) = ready_book(client, app)
    old_pose = get_page(app, p1["id"]).pose_generation_id
    setup_edit(client, p1["id"], scene_prompt="雪地", action_prompt="坐著")
    produce(client, b["id"])
    run_all(client, app)
    page = get_page(app, p1["id"])
    assert page.attention == ATTENTION_REVIEW_POSE
    take_pose, auto_bg = page.pose_generation_id, page.auto_background_generation_id
    # 舊編輯器選回舊的動作與上傳背景（不帶 oneclick：不清軟標記）：之後才有東西可以還原
    picked(client, p1["id"], "pose", old_pose)
    assert upload_bg(client, p1["id"], color_png()).status_code == 201
    assert get_page(app, p1["id"]).attention == ATTENTION_REVIEW_POSE
    r = client.post(f"/api/pages/{p1['id']}/restore", json={"item": "background"})
    assert r.status_code == 200, r.text
    page = get_page(app, p1["id"])
    assert page.background_generation_id == auto_bg
    assert page.attention == ATTENTION_REVIEW_POSE  # 還原背景不動它
    r = client.post(f"/api/pages/{p1['id']}/restore", json={"item": "pose"})
    assert r.status_code == 200, r.text
    page = get_page(app, p1["id"])
    assert page.pose_generation_id == take_pose
    assert (page.attention, page.attention_detail) == (None, None)


def test_landed_text_is_none_when_generation_vanished(client, app):
    """auto_select 的文字保護：被選上的那筆剛好被刪掉（例如刪書和 worker 撞在一起）時不會丟 AttributeError。"""
    from sqlmodel import Session

    from app.services.pipeline import _landed_text

    with Session(app.state.db) as s:
        assert _landed_text(s, 987654321) is None


# ---- include_character（不放主角）：改了要重畫背景 -----------------------------------------


def test_patch_from_setup_marks_redraw(client, app):
    _, _, (p1, p2, _) = ready_book(client, app)
    # 沒帶 from_setup：改了不標
    patch(client, p2["id"], {"include_character": False})
    assert flags(app, p2["id"]) == (False, False)
    # 值沒變：不標
    setup_edit(client, p2["id"], include_character=False)
    assert flags(app, p2["id"]) == (False, False)
    # 有 AI 背景、值變了：標
    out = setup_edit(client, p1["id"], include_character=False)
    assert out["include_character"] is False and out["redraw_background"] is True
    assert flags(app, p1["id"]) == (True, False)


def test_patch_from_setup_include_character_on_uploaded_background_no_mark(client, app):
    login(client, app)
    _, (p1,) = book_with_pages(client, app, [("公園", "揮手")])
    assert upload_bg(client, p1["id"], color_png()).status_code == 201
    out = setup_edit(client, p1["id"], include_character=False)
    assert out["include_character"] is False
    assert flags(app, p1["id"]) == (False, False)
