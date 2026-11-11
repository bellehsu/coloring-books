"""Phase 13 Task 4：C 處理一頁的復原／重做（page_history）、「還原成自動畫的那張」，以及 C 的修改
（?oneclick=true）先存快照、再排頁面流程。

C 的修改都帶 ?oneclick=true；不帶的是舊編輯器（進階編輯），行為不變。
"""

import json

import pytest
from fastapi.testclient import TestClient
from sqlmodel import col, select

from app.config import load_settings
from app.main import create_app
from app.models import Page, PageHistory, PageStatus, StrokeLayer
from app.services import history
from app.services.page_lineart import save_strokes
from tests.fake_engine import FakeEngine
from tests.test_api_books import run_jobs
from tests.test_api_compose import composites, gen_background, gen_pose, png_bytes
from tests.test_api_lineart import get_page
from tests.test_pipeline import drain, layer, produced, queued


@pytest.fixture
def app(settings_file):
    return create_app(load_settings(settings_file), engine=FakeEngine(), start_worker=False)


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


C = {"oneclick": "true"}
SCRIBBLE = [{"op": "draw", "width_px": 12.0, "points": [[0.1, 0.1], [0.3, 0.3]]}]
ERASE = [{"op": "erase", "width_px": 20.0, "points": [[0.5, 0.5], [0.6, 0.6]]}]
MOVED = {"cx": 0.3, "cy": 0.6, "height": 0.5, "flip": False}


def user_layer(app, gen_id):
    return [
        {"op": s.op, "width_px": s.width_px, "points": [list(p) for p in s.points]}
        for s in layer(app, gen_id, StrokeLayer.USER)
    ]


def draw(client, page, strokes, **params):
    r = client.put(f"/api/pages/{page['id']}/lineart/edits", params={**C, **params},
                   json={"generation_id": page["selected_generation_id"], "strokes": strokes})
    assert r.status_code == 200, r.text
    return r


def page_json(client, pid):
    with client.app.state.sessions() as s:
        book_id = s.get(Page, pid).book_id
    return next(x for x in client.get(f"/api/books/{book_id}/pages").json() if x["id"] == pid)


def ready(client, app, **kw):
    """produced（背景＋角色動作產生完、流程跑完、可以印）並回傳目前的 PageOut。"""
    b, p, bg, pose = produced(client, app, **kw)
    return b, page_json(client, p["id"]), bg, pose


def upload_c(client, pid):
    r = client.post(f"/api/pages/{pid}/backgrounds/upload", params=C,
                    files={"file": ("mine.png", png_bytes(), "image/png")})
    assert r.status_code == 201, r.text
    return r


def undo(client, pid, status=200):
    r = client.post(f"/api/pages/{pid}/undo")
    assert r.status_code == status, r.text
    return r.json()


def redo(client, pid, status=200):
    r = client.post(f"/api/pages/{pid}/redo")
    assert r.status_code == status, r.text
    return r.json()


def restore(client, pid, item, status=200):
    r = client.post(f"/api/pages/{pid}/restore", json={"item": item})
    assert r.status_code == status, r.text
    return r.json()


def history_rows(app, pid) -> list[PageHistory]:
    with app.state.sessions() as s:
        return list(s.exec(select(PageHistory).where(PageHistory.page_id == pid)
                           .order_by(col(PageHistory.seq))).all())


# ---- 復原／重做：選圖、位置、筆畫、重新合成 -----------------------------------------------


def test_undo_redo_across_strokes_placement_and_recompose(client, app):
    b, page, bg, pose = ready(client, app)
    pid = page["id"]
    c1 = page["selected_generation_id"]
    assert page["status"] == "passed"
    assert page["can_undo"] is False and page["can_redo"] is False

    # 使用者的筆畫：先存快照，再排頁面流程（不退回草稿，流程跑完仍可以印）
    draw(client, page, SCRIBBLE)
    out = page_json(client, pid)
    assert out["can_undo"] is True and out["has_user_strokes"] is True
    assert queued(app, pid) and get_page(app, pid).status == PageStatus.PASSED
    drain(client, app)
    assert user_layer(app, c1) == SCRIBBLE
    assert get_page(app, pid).status == PageStatus.PASSED

    # 拖角色（位置）：重新合成，新的合成圖沒有使用者筆畫（不跳對話框直接清掉）
    r = client.put(f"/api/pages/{pid}/placement", params=C, json=MOVED)
    assert r.status_code == 200, r.text
    assert queued(app, pid)
    drain(client, app)
    pg = get_page(app, pid)
    c2 = pg.selected_generation_id
    assert c2 != c1 and pg.status == PageStatus.PASSED
    assert user_layer(app, c2) == []
    assert page_json(client, pid)["has_user_strokes"] is False

    # 復原：回到原本的位置，流程重新合成（沿用 c1），然後把筆畫放回去
    out = undo(client, pid)
    assert out["placement"] is None and out["can_redo"] is True and out["can_undo"] is True
    assert queued(app, pid)
    drain(client, app)
    pg = get_page(app, pid)
    assert pg.selected_generation_id == c1 and pg.status == PageStatus.PASSED
    assert user_layer(app, c1) == SCRIBBLE
    assert pg.pending_user_strokes is None

    # 重做：又回到拖過的位置（c2），筆畫清掉
    out = redo(client, pid)
    assert out["placement"]["cx"] == MOVED["cx"] and out["can_redo"] is False
    drain(client, app)
    pg = get_page(app, pid)
    assert pg.selected_generation_id == c2 and user_layer(app, c2) == []

    # 再復原兩次：先回到有筆畫的 c1，再回到沒有筆畫的 c1
    undo(client, pid)
    drain(client, app)
    assert get_page(app, pid).selected_generation_id == c1 and user_layer(app, c1) == SCRIBBLE
    out = undo(client, pid)
    assert out["can_undo"] is False and out["can_redo"] is True
    drain(client, app)
    assert user_layer(app, c1) == [] and get_page(app, pid).status == PageStatus.PASSED
    undo(client, pid, status=409)

    # 重做回到有筆畫，然後新的修改截掉後面的重做
    redo(client, pid)
    drain(client, app)
    assert user_layer(app, c1) == SCRIBBLE
    r = client.patch(f"/api/pages/{pid}", params=C, json={"caption_zh": "小熊去公園"})
    assert r.status_code == 200 and r.json()["can_redo"] is False and r.json()["can_undo"] is True
    redo(client, pid, status=409)
    # 文字的復原：不用重新合成
    drain(client, app)
    out = undo(client, pid)
    assert out["caption_zh"] is None and not queued(app, pid)
    assert user_layer(app, c1) == SCRIBBLE


def test_undo_before_pipeline_runs_restores_strokes(client, app):
    """拖完馬上復原（流程還沒跑）：等著寫回的筆畫跟著快照走，流程跑完筆畫還在。"""
    b, page, bg, pose = ready(client, app)
    pid, c1 = page["id"], page["selected_generation_id"]
    draw(client, page, SCRIBBLE)
    drain(client, app)
    client.put(f"/api/pages/{pid}/placement", params=C, json=MOVED)
    assert get_page(app, pid).pending_user_strokes == "[]"
    undo(client, pid)
    assert json.loads(get_page(app, pid).pending_user_strokes) == SCRIBBLE
    redo(client, pid)
    assert get_page(app, pid).pending_user_strokes == "[]"
    undo(client, pid)
    drain(client, app)
    pg = get_page(app, pid)
    assert pg.selected_generation_id == c1 and user_layer(app, c1) == SCRIBBLE
    assert pg.pending_user_strokes is None and pg.status == PageStatus.PASSED


def test_select_background_then_undo(client, app):
    b, page, bg1, pose = ready(client, app)
    pid = page["id"]
    bg2 = gen_background(client, app, pid)  # 舊路徑產生：自動選圖換成最新的
    drain(client, app)
    assert get_page(app, pid).background_generation_id == bg2
    draw(client, page_json(client, pid), SCRIBBLE)
    drain(client, app)
    with_strokes = get_page(app, pid).selected_generation_id

    r = client.post(f"/api/pages/{pid}/background", params=C, json={"generation_id": bg1})
    assert r.status_code == 200, r.text
    assert r.json()["can_undo"] is True and queued(app, pid)
    drain(client, app)
    pg = get_page(app, pid)
    assert pg.background_generation_id == bg1 and pg.auto_background_generation_id == bg2
    assert user_layer(app, pg.selected_generation_id) == [] and pg.status == PageStatus.PASSED

    out = undo(client, pid)
    assert out["background_generation_id"] == bg2
    drain(client, app)
    pg = get_page(app, pid)
    assert pg.selected_generation_id == with_strokes and user_layer(app, with_strokes) == SCRIBBLE


def test_old_editor_paths_do_not_push_snapshots_or_enqueue(client, app):
    b, page, bg, pose = ready(client, app)
    pid = page["id"]
    r = client.put(f"/api/pages/{pid}/placement", json=MOVED)
    assert r.status_code == 200 and r.json()["can_undo"] is False
    client.patch(f"/api/pages/{pid}", json={"caption_zh": "字"})
    client.put(f"/api/pages/{pid}/lineart/edits",
               json={"generation_id": page["selected_generation_id"], "strokes": SCRIBBLE})
    assert history_rows(app, pid) == [] and not queued(app, pid)
    assert get_page(app, pid).status == PageStatus.DRAFT  # 舊編輯器修線仍退回草稿


def test_unchanged_c_mutations_push_nothing(client, app):
    b, page, bg, pose = ready(client, app)
    pid = page["id"]
    client.post(f"/api/pages/{pid}/background", params=C, json={"generation_id": bg})
    client.patch(f"/api/pages/{pid}", params=C, json={"caption_zh": None})
    draw(client, page, [])
    assert history_rows(app, pid) == [] and not queued(app, pid)


def test_drawing_while_recompose_pending_is_rejected(client, app):
    b, page, bg, pose = ready(client, app)
    pid = page["id"]
    client.put(f"/api/pages/{pid}/placement", params=C, json=MOVED)
    r = client.put(f"/api/pages/{pid}/lineart/edits", params=C,
                   json={"generation_id": page["selected_generation_id"], "strokes": SCRIBBLE})
    assert r.status_code == 409 and "重新合成" in r.json()["detail"]


def test_history_cap(client, app):
    b, page, bg, pose = ready(client, app)
    pid = page["id"]
    for i in range(1, 56):
        r = client.patch(f"/api/pages/{pid}", params=C, json={"caption_zh": f"c{i}"})
        assert r.status_code == 200
        assert len(history_rows(app, pid)) <= history.MAX_SNAPSHOTS
    assert len(history_rows(app, pid)) == history.MAX_SNAPSHOTS
    n = 0
    while client.post(f"/api/pages/{pid}/undo").status_code == 200:
        n += 1
        assert len(history_rows(app, pid)) <= history.MAX_SNAPSHOTS
    assert n == history.MAX_SNAPSHOTS - 1
    assert get_page(app, pid).caption_zh == f"c{55 - n}"
    for _ in range(n):
        redo(client, pid)
    assert get_page(app, pid).caption_zh == "c55"
    redo(client, pid, status=409)


def test_undo_keeps_slot_when_snapshot_image_was_discarded(client, app):
    b, page, bg1, pose = ready(client, app)
    pid = page["id"]
    bg2 = gen_background(client, app, pid)
    drain(client, app)
    client.post(f"/api/pages/{pid}/background", params=C, json={"generation_id": bg1})
    drain(client, app)
    # bg2 已不在用：捨棄它，再復原到用 bg2 的狀態
    assert client.patch(f"/api/generations/{bg2}", json={"discarded": True}).status_code == 200
    out = undo(client, pid)
    assert out["background_generation_id"] == bg1  # 圖已不能用：保留目前的


# ---- 還原成自動畫的那張 -----------------------------------------------------------------


def test_restore_background_after_upload(client, app):
    b, page, bg, pose = ready(client, app)
    pid = page["id"]
    r = upload_c(client, pid)
    up = r.json()["background_generation_id"]
    assert r.json()["can_undo"] is True and queued(app, pid)
    drain(client, app)
    assert get_page(app, pid).background_generation_id == up

    out = restore(client, pid, "background")
    assert out["background_generation_id"] == bg and out["auto_background_generation_id"] == bg
    drain(client, app)
    pg = get_page(app, pid)
    comp = next(g for g in composites(app, pid) if g.id == pg.selected_generation_id)
    assert json.loads(comp.params_json)["background_generation_id"] == bg
    assert pg.status == PageStatus.PASSED
    # 還原也能復原
    undo(client, pid)
    assert get_page(app, pid).background_generation_id == up
    # 已經是自動畫的那張：什麼都不做
    redo(client, pid)
    before = len(history_rows(app, pid))
    restore(client, pid, "background")
    assert len(history_rows(app, pid)) == before


def test_restore_pose(client, app):
    b, page, bg, pose1 = ready(client, app)
    pid = page["id"]
    pose2 = gen_pose(client, app, pid)
    drain(client, app)
    assert get_page(app, pid).auto_pose_generation_id == pose2
    client.post(f"/api/pages/{pid}/pose", params=C, json={"generation_id": pose1})
    drain(client, app)
    assert get_page(app, pid).pose_generation_id == pose1
    out = restore(client, pid, "pose")
    assert out["pose_generation_id"] == pose2
    drain(client, app)
    assert get_page(app, pid).status == PageStatus.PASSED


def test_restore_when_auto_was_discarded_falls_back_to_newest_ai_image(client, app):
    b, page, bg1, pose = ready(client, app)
    pid = page["id"]
    bg2 = gen_background(client, app, pid)
    drain(client, app)
    up = upload_c(client, pid).json()["background_generation_id"]
    drain(client, app)
    assert client.patch(f"/api/generations/{bg2}", json={"discarded": True}).status_code == 200
    out = restore(client, pid, "background")
    assert out["background_generation_id"] == bg1 and out["auto_background_generation_id"] == bg1
    # 沒有任何能用的 AI 背景：409
    client.post(f"/api/pages/{pid}/background", params=C, json={"generation_id": up})
    assert client.patch(f"/api/generations/{bg1}", json={"discarded": True}).status_code == 200
    r = client.post(f"/api/pages/{pid}/restore", json={"item": "background"})
    assert r.status_code == 409 and "背景" in r.json()["detail"]


def test_restore_lines_clears_user_strokes_and_undo_brings_them_back(client, app):
    b, page, bg, pose = ready(client, app)
    pid, c1 = page["id"], page["selected_generation_id"]
    draw(client, page, ERASE)
    drain(client, app)
    auto = layer(app, c1, StrokeLayer.AUTO)
    out = restore(client, pid, "lines")
    assert out["has_user_strokes"] is False and user_layer(app, c1) == []
    assert queued(app, pid)
    drain(client, app)
    assert layer(app, c1, StrokeLayer.AUTO) == auto  # auto 層留著（流程重算，結果一樣）
    assert get_page(app, pid).status == PageStatus.PASSED
    undo(client, pid)
    assert user_layer(app, c1) == ERASE
    # 沒有使用者筆畫：還原線條什麼都不做
    redo(client, pid)
    before = len(history_rows(app, pid))
    restore(client, pid, "lines")
    assert len(history_rows(app, pid)) == before


def test_restore_validates_item(client, app):
    b, page, bg, pose = ready(client, app)
    r = client.post(f"/api/pages/{page['id']}/restore", json={"item": "caption"})
    assert r.status_code == 422


# ---- 換一張／AI 再畫一張：新圖一定換上去 ------------------------------------------------


def test_c_regenerate_takes_new_image_even_over_user_pick(client, app):
    b, page, bg, pose1 = ready(client, app)
    pid = page["id"]
    pose2 = gen_pose(client, app, pid)
    drain(client, app)
    client.post(f"/api/pages/{pid}/pose", params=C, json={"generation_id": pose1})  # 使用者選回舊的
    drain(client, app)
    n = len(history_rows(app, pid))
    r = client.post(f"/api/pages/{pid}/poses", params=C, json={"action": "jumping"})
    assert r.status_code == 202
    pose3 = r.json()["id"]
    assert len(history_rows(app, pid)) == n  # 圖還沒好：還沒有改到頁面
    run_jobs(client, app)
    pg = get_page(app, pid)
    assert pg.pose_generation_id == pose3 and pg.auto_pose_generation_id == pose3
    assert len(history_rows(app, pid)) == n + 1  # 換上去之前存快照
    drain(client, app)
    assert get_page(app, pid).status == PageStatus.PASSED
    out = undo(client, pid)
    assert out["pose_generation_id"] == pose1
    # 舊編輯器的「再產生」：使用者選的不換
    gen_pose(client, app, pid)
    assert get_page(app, pid).pose_generation_id == pose1
    assert pose2


def test_c_regenerate_background_replaces_upload(client, app):
    """「AI 再畫一張」：上傳的背景換回 AI 畫的（切回 AI）。"""
    b, page, bg, pose = ready(client, app)
    pid = page["id"]
    upload_c(client, pid)
    drain(client, app)
    r = client.post(f"/api/pages/{pid}/backgrounds", params=C)
    assert r.status_code == 202
    run_jobs(client, app)
    drain(client, app)
    pg = get_page(app, pid)
    assert pg.background_generation_id == r.json()["id"] == pg.auto_background_generation_id
    assert pg.status == PageStatus.PASSED


# ---- 線稿的 key 變了（紙張、版本）：流程重跑，不悄悄退回 --------------------------------------


def test_paper_size_change_reruns_pipeline_instead_of_demoting(client, app):
    b, page, bg, pose = ready(client, app)
    pid = page["id"]
    old_key = get_page(app, pid).lineart_key
    r = client.patch(f"/api/books/{b['id']}", json={"paper_size": "b5"})
    assert r.status_code == 200
    assert get_page(app, pid).status == PageStatus.PASSED
    assert queued(app, pid)
    drain(client, app)
    pg = get_page(app, pid)
    assert pg.status == PageStatus.PASSED and pg.lineart_key != old_key


def test_key_change_on_pipeline_page_enqueues_instead_of_demoting(client, app):
    b, page, bg, pose = ready(client, app)
    pid, c1 = page["id"], page["selected_generation_id"]
    with app.state.sessions() as s:  # 補線不見了（例如版本更新後缺口位置不同）：線稿有缺口
        save_strokes(s, pid, c1, [], layer=StrokeLayer.AUTO)
        s.commit()
    qa = client.get(f"/api/pages/{pid}/qa").json()
    assert qa["gap_count"] > 0
    assert get_page(app, pid).status == PageStatus.PASSED
    assert queued(app, pid)
    drain(client, app)
    assert client.get(f"/api/pages/{pid}/qa").json()["gap_count"] == 0


# ---- Fix round 1：等著重新合成（recompose_pending）、舊編輯器優先、還原的圖不被換掉 ------------------


def test_recompose_pending_flag(client, app):
    """拖完、復原（流程還沒跑）：PageOut.recompose_pending 讓 C 先停用畫筆；流程跑完就沒了。"""
    b, page, bg, pose = ready(client, app)
    pid = page["id"]
    assert page["recompose_pending"] is False
    draw(client, page, SCRIBBLE)
    drain(client, app)
    r = client.put(f"/api/pages/{pid}/placement", params=C, json=MOVED)
    assert r.json()["recompose_pending"] is True
    out = undo(client, pid)  # 流程還沒跑：位置回到 c1 的，compose_pending 是 False，但筆畫還沒寫回
    assert out["recompose_pending"] is True and out["compose_pending"] is False
    drain(client, app)
    assert page_json(client, pid)["recompose_pending"] is False


def test_failed_recompose_says_retry_and_retry_reapplies_strokes(client, app, monkeypatch):
    from app.services import page_compose, pipeline

    b, page, bg, pose = ready(client, app)
    pid, c1 = page["id"], page["selected_generation_id"]
    draw(client, page, SCRIBBLE)
    drain(client, app)
    client.put(f"/api/pages/{pid}/placement", params=C, json=MOVED)
    drain(client, app)
    undo(client, pid)  # 等著把 SCRIBBLE 寫回 c1

    def boom(*a, **k):
        raise page_compose.ComposeError("圖片檔案遺失，請重新產生")

    monkeypatch.setattr(pipeline, "compose_page", boom)
    drain(client, app)
    out = page_json(client, pid)
    assert out["attention"] == "failed" and out["recompose_pending"] is True and not queued(app, pid)
    r = client.put(f"/api/pages/{pid}/lineart/edits", params=C,
                   json={"generation_id": out["selected_generation_id"], "strokes": ERASE})
    assert r.status_code == 409 and r.json()["detail"] == "這一頁合成失敗，請先按「再試一次」"
    # 再試一次（繼續做也會排這頁）：筆畫寫回、可以印
    monkeypatch.undo()
    r = client.post(f"/api/books/{b['id']}/produce")
    assert r.status_code == 202 and pid in r.json()["pipeline_pages"]
    drain(client, app)
    pg = get_page(app, pid)
    assert pg.selected_generation_id == c1 and user_layer(app, c1) == SCRIBBLE
    assert pg.pending_user_strokes is None and pg.attention is None and pg.status == PageStatus.PASSED


def test_old_editor_compose_and_draw_win_over_pending(client, app):
    b, page, bg, pose = ready(client, app)
    pid = page["id"]
    client.put(f"/api/pages/{pid}/placement", params=C, json=MOVED)  # 等著寫 "[]"
    r = client.post(f"/api/pages/{pid}/compose")
    assert r.status_code == 200 and r.json()["recompose_pending"] is False
    gid = r.json()["selected_generation_id"]
    r = client.put(f"/api/pages/{pid}/lineart/edits", json={"generation_id": gid, "strokes": SCRIBBLE})
    assert r.status_code == 200
    drain(client, app)
    assert get_page(app, pid).selected_generation_id == gid and user_layer(app, gid) == SCRIBBLE


def test_old_editor_strokes_clear_pending(client, app):
    b, page, bg, pose = ready(client, app)
    pid, c1 = page["id"], page["selected_generation_id"]
    client.put(f"/api/pages/{pid}/placement", params=C, json=MOVED)
    r = client.put(f"/api/pages/{pid}/lineart/edits", json={"generation_id": c1, "strokes": SCRIBBLE})
    assert r.status_code == 200
    assert get_page(app, pid).pending_user_strokes is None


def test_restored_background_sticks_over_newer_ai_image(client, app):
    """還原的背景不被流程換成更新的 AI 背景（舊編輯器再產生、使用者沒選的那張）。"""
    b, page, bg1, pose = ready(client, app)
    pid = page["id"]
    upload_c(client, pid)
    drain(client, app)
    bg2 = gen_background(client, app, pid)  # 使用者選了上傳的：bg2 不換上去
    drain(client, app)
    assert get_page(app, pid).auto_background_generation_id == bg1
    out = restore(client, pid, "background")
    assert out["background_generation_id"] == bg1
    drain(client, app)
    pg = get_page(app, pid)
    assert pg.background_generation_id == bg1 and pg.auto_background_generation_id == bg1 and bg2
    assert pg.status == PageStatus.PASSED


# ---- 「這頁可以印了 ✓」（POST /pages/{id}/accept）、再試一次、C 的自動補線 ---------------------


def accept(client, pid, status=200):
    r = client.post(f"/api/pages/{pid}/accept")
    assert r.status_code == status, r.text
    return r.json()


def test_accept_clears_attention_and_keeps_passed(client, app):
    from tests.test_api_lineart import set_page

    b, page, bg, pose = ready(client, app)
    pid = page["id"]
    passed_at = get_page(app, pid).passed_at
    set_page(app, pid, attention="failed", attention_detail="圖片產生失敗，請重新產生")
    out = accept(client, pid)
    assert out["attention"] is None and out["attention_detail"] is None and out["status"] == "passed"
    pg = get_page(app, pid)
    assert pg.passed_at == passed_at  # 已經可以印：保留原本的通過時間
    assert history_rows(app, pid) == []  # 不是內容的改動：不存快照、不排流程
    assert not queued(app, pid)


def test_accept_draft_composite_becomes_passed(client, app):
    from tests.test_api_lineart import set_page

    b, page, bg, pose = ready(client, app)
    pid = page["id"]
    set_page(app, pid, status=PageStatus.DRAFT, passed_at=None, attention="failed", attention_detail="x")
    out = accept(client, pid)
    assert out["status"] == "passed" and out["attention"] is None
    assert get_page(app, pid).passed_at is not None


def test_accept_without_selection_is_409(client, app):
    from tests.test_pipeline import book_page

    b, p = book_page(client, app)
    r = client.post(f"/api/pages/{p['id']}/accept")
    assert r.status_code == 409 and r.json()["detail"] == "這一頁還沒有選定圖片"


def test_retry_reruns_passed_page_with_failed_attention(client, app):
    """「再試一次」（C 呼叫 /produce）：已經可以印、但後來失敗（例如換一張失敗）的頁也重跑流程，跑完清掉 attention。"""
    from tests.test_api_lineart import set_page

    b, page, bg, pose = ready(client, app)
    pid = page["id"]
    set_page(app, pid, attention="failed", attention_detail="圖片產生失敗，請重新產生")
    r = client.post(f"/api/books/{b['id']}/produce")
    assert r.status_code == 202 and pid in r.json()["pipeline_pages"]
    drain(client, app)
    pg = get_page(app, pid)
    assert pg.attention is None and pg.status == PageStatus.PASSED


def test_c_autofix_keeps_pipeline_page_passed(client, app, monkeypatch):
    """C 的「自動補線」（?oneclick=true）：流程做的頁補了線也不退回草稿；舊編輯器照舊退回。"""
    from app.api import lineart as lineart_api
    from app.services.lineart_render import Stroke
    from tests.test_api_lineart import _fake_report, set_page
    from app.services import page_lineart

    b, page, bg, pose = ready(client, app)
    pid, gid = page["id"], page["selected_generation_id"]
    monkeypatch.setattr(page_lineart, "evaluate", lambda ink, lw: _fake_report(1))
    bridge = Stroke("draw", 11.0, ((0.1, 0.3), (0.1, 0.31)))
    monkeypatch.setattr(lineart_api, "plan_autofix", lambda *a, **k: [bridge])

    set_page(app, pid, lineart_key=None)  # 用假的 QA（一個缺口）重算
    r = client.post(f"/api/pages/{pid}/lineart/autofix", params=C, json={"generation_id": gid})
    assert r.status_code == 200, r.text
    assert r.json()["autofix_added"] == 1
    assert get_page(app, pid).status == PageStatus.PASSED

    set_page(app, pid, lineart_key=None)
    r = client.post(f"/api/pages/{pid}/lineart/autofix", json={"generation_id": gid})
    assert r.status_code == 200 and r.json()["autofix_added"] == 1
    assert get_page(app, pid).status == PageStatus.DRAFT
