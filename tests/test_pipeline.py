"""Phase 13 頁面流程（services/pipeline.py）與 worker 整合：選圖 → 合成 → 線稿 → 自動補線（auto 層）→ 可以印。"""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from sqlmodel import col, select

from app.config import load_settings
from app.main import create_app
from app.models import (
    Generation, GenKind, GenStatus, LineartEdit, Page, PageStatus, PageTask, PageTaskStatus, StrokeLayer,
    utcnow,
)
from app.services import pipeline
from app.services.compose import DEFAULT_PLACEMENT, Placement
from app.services.engine import EngineError, EngineUnavailable
from app.services.lineart_render import Stroke
from app.services.page_lineart import load_strokes, save_strokes
from tests.fake_engine import FakeEngine
from tests.test_api_books import run_jobs
from tests.test_api_compose import composites, gen_background, gen_pose, picked, png_bytes, upload_bg
from tests.test_api_lineart import A5_HOME, color_png, get_page, set_page
from tests.test_api_pages import book_with_character, new_page


@pytest.fixture
def app(settings_file):
    return create_app(load_settings(settings_file), engine=FakeEngine(), start_worker=False)


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


# ---- 小工具 ------------------------------------------------------------------------


def book_page(client, app, *, gap=12, scene="公園", action="揮手"):
    """建 A5 書＋角色＋一頁（彩色方塊圖；gap > 0 時背景上緣有一個會漏色的缺口）。"""
    app.state.engine.image = color_png(gap=gap)
    b, _ = book_with_character(client, app, **A5_HOME)
    p = new_page(client, b["id"], scene_prompt=scene, action_prompt=action)
    return b, p


def drain(client, app) -> int:
    return client.portal.call(app.state.worker.run_page_tasks)


def tasks(app, page_id=None) -> list[PageTask]:
    with app.state.sessions() as s:
        q = select(PageTask).order_by(col(PageTask.id))
        if page_id is not None:
            q = q.where(PageTask.page_id == page_id)
        return list(s.exec(q).all())


def queued(app, page_id) -> list[PageTask]:
    return [t for t in tasks(app, page_id) if t.status == PageTaskStatus.QUEUED.value]


def layer(app, gen_id, which) -> list[Stroke]:
    with app.state.sessions() as s:
        return load_strokes(s, gen_id, layer=which)


def run_pipeline(app, page_id):
    with app.state.sessions() as s:
        return pipeline.run_page_pipeline(s, app.state.storage, page_id)


def enqueue(app, page_id) -> bool:
    with app.state.sessions() as s:
        added = pipeline.enqueue_page_task(s, page_id)
        s.commit()
        return added


def produced(client, app, **kw):
    """背景與角色動作都產生完（worker 自動選用並排入頁面流程），再跑完頁面流程。"""
    b, p = book_page(client, app, **kw)
    bg = gen_background(client, app, p["id"])
    pose = gen_pose(client, app, p["id"])
    drain(client, app)
    return b, p, bg, pose


def test_finished_tasks_pruned_to_five(client, app):
    b, p = book_page(client, app)
    other = new_page(client, b["id"], scene_prompt="海邊", action_prompt="跳")["id"]
    pid = p["id"]
    with app.state.sessions() as s:
        ids = []
        for _ in range(8):
            t = PageTask(page_id=pid, status=PageTaskStatus.RUNNING.value)
            s.add(t)
            s.flush()
            ids.append(t.id)
        s.add(PageTask(page_id=pid, status=PageTaskStatus.QUEUED.value))
        oid = PageTask(page_id=other, status=PageTaskStatus.SUCCEEDED.value)
        s.add(oid)
        s.commit()
        other_ids = [oid.id]
    for i, tid in enumerate(ids):
        with app.state.sessions() as s:
            pipeline.finish_page_task(s, tid, PageTaskStatus.SUCCEEDED if i % 2 else PageTaskStatus.FAILED, None)
    mine = tasks(app, pid)
    finished = [t.id for t in mine if t.status in (PageTaskStatus.SUCCEEDED.value, PageTaskStatus.FAILED.value)]
    assert finished == ids[-5:]  # 只留最新的 5 筆
    assert len([t for t in mine if t.status == PageTaskStatus.QUEUED.value]) == 1  # 進行中的不動
    assert [t.id for t in tasks(app, other)] == other_ids  # 別頁不受影響


def _add_finished_run(app, pid, n, started):
    """在 started 之後完成 n 筆工作（RUNNING → finish_page_task），回傳 id。"""
    ids = []
    for _ in range(n):
        with app.state.sessions() as s:
            t = PageTask(page_id=pid, status=PageTaskStatus.RUNNING.value)
            s.add(t)
            s.commit()
            tid = t.id
        with app.state.sessions() as s:
            pipeline.finish_page_task(s, tid, PageTaskStatus.SUCCEEDED, None)
        ids.append(tid)
    return ids


def test_prune_keeps_in_run_finished_tasks(client, app):
    from datetime import timedelta
    from app.models import Book, utcnow

    b, p = book_page(client, app)
    pid = p["id"]
    with app.state.sessions() as s:
        book = s.get(Book, b["id"])
        book.produce_started_at = utcnow() - timedelta(minutes=5)
        s.add(book)
        old = PageTask(page_id=pid, status=PageTaskStatus.SUCCEEDED.value,
                       created_at=book.produce_started_at - timedelta(days=1))
        s.add(old)
        s.commit()
        old_id = old.id
    ids = _add_finished_run(app, pid, 7, None)
    kept = [t.id for t in tasks(app, pid)]
    assert kept == ids  # 這一輪的 7 筆都留著；更早的（超過 5 筆之外）才刪
    assert old_id not in kept


def test_prune_without_run_start_keeps_five(client, app):
    b, p = book_page(client, app)
    ids = _add_finished_run(app, p["id"], 7, None)
    assert [t.id for t in tasks(app, p["id"])] == ids[-5:]


# ---- 完整流程 ----------------------------------------------------------------------


def test_happy_path_end_to_end(client, app):
    b, p = book_page(client, app)
    pid = p["id"]
    bg = gen_background(client, app, pid)
    # 背景好了：馬上自動選用、記下 auto，排一個頁面流程
    pg = get_page(app, pid)
    assert pg.background_generation_id == bg and pg.auto_background_generation_id == bg
    assert len(queued(app, pid)) == 1
    pose = gen_pose(client, app, pid)
    pg = get_page(app, pid)
    assert pg.pose_generation_id == pose and pg.auto_pose_generation_id == pose
    assert len(queued(app, pid)) == 1  # 兩次成功仍只有一個排隊中的工作

    assert drain(client, app) == 1
    pg = get_page(app, pid)
    assert pg.status == PageStatus.PASSED and pg.passed_at is not None
    assert pg.attention is None and pg.attention_detail is None
    assert pg.character_check is None  # 不需要「對」
    [comp] = composites(app, pid)
    assert pg.selected_generation_id == comp.id and comp.status == GenStatus.SUCCEEDED
    params = json.loads(comp.params_json)
    assert params["background_generation_id"] == bg and params["pose_generation_id"] == pose
    assert Placement.from_dict(params["placement"]).key() == DEFAULT_PLACEMENT.key()
    # 自動補線寫在 auto 層，使用者層不動；線稿已經是最新的、缺口接好了
    assert layer(app, comp.id, StrokeLayer.AUTO)
    assert layer(app, comp.id, StrokeLayer.USER) == []
    qa = client.get(f"/api/pages/{pid}/qa").json()
    assert qa["gap_count"] == 0 and qa["status"] == "passed"
    out = next(x for x in client.get(f"/api/books/{b['id']}/pages").json() if x["id"] == pid)
    assert out["status"] == "passed" and out["compose_pending"] is False
    [t] = tasks(app, pid)
    assert t.status == PageTaskStatus.SUCCEEDED.value and t.error is None
    # 匯出（只收可以印的頁）包含這一頁
    summary = client.get(f"/api/books/{b['id']}/export/summary").json()
    assert summary["skipped"] == []


def test_waits_until_both_images_exist(client, app):
    b, p = book_page(client, app)
    pid = p["id"]
    gen_background(client, app, pid)
    assert drain(client, app) == 1
    pg = get_page(app, pid)
    assert pg.status == PageStatus.DRAFT and pg.selected_generation_id is None
    assert pg.attention is None
    assert composites(app, pid) == []
    assert tasks(app, pid)[0].status == PageTaskStatus.SUCCEEDED.value


def test_rerun_reuses_composite_and_overwrites_auto_layer(client, app):
    b, p, bg, pose = produced(client, app)
    pid = p["id"]
    comp = get_page(app, pid).selected_generation_id
    auto = layer(app, comp, StrokeLayer.AUTO)
    # auto 層被塞了別的筆畫（例如舊編輯器按了好幾次自動補線）；使用者層有一筆
    junk = [Stroke("draw", 11.0, ((0.1, 0.1), (0.2, 0.2)))] * 3
    user = [Stroke("erase", 4.0, ((0.01, 0.01),))]
    with app.state.sessions() as s:
        save_strokes(s, pid, comp, auto + junk, layer=StrokeLayer.AUTO)
        save_strokes(s, pid, comp, user, layer=StrokeLayer.USER)
        s.commit()
    assert run_pipeline(app, pid) == pipeline.PASSED
    pg = get_page(app, pid)
    assert pg.selected_generation_id == comp and len(composites(app, pid)) == 1
    assert layer(app, comp, StrokeLayer.AUTO) == auto  # 整層覆寫，不是附加
    assert layer(app, comp, StrokeLayer.USER) == user
    assert pg.status == PageStatus.PASSED
    # 再跑一次：什麼都沒變
    key = pg.lineart_key
    assert run_pipeline(app, pid) == pipeline.PASSED
    pg = get_page(app, pid)
    assert pg.lineart_key == key and layer(app, comp, StrokeLayer.AUTO) == auto
    with app.state.sessions() as s:
        rows = s.exec(select(LineartEdit).where(LineartEdit.generation_id == comp)).all()
    assert len(rows) == 2


def test_uses_saved_placement(client, app):
    b, p = book_page(client, app)
    pid = p["id"]
    mine = {"cx": 0.4, "cy": 0.6, "height": 0.5, "flip": True}
    assert client.put(f"/api/pages/{pid}/placement", json=mine).status_code == 200
    gen_background(client, app, pid)
    gen_pose(client, app, pid)
    drain(client, app)
    [comp] = composites(app, pid)
    assert json.loads(comp.params_json)["placement"] == mine
    assert json.loads(get_page(app, pid).placement_json) == mine  # 位置是使用者的，不改
    assert get_page(app, pid).status == PageStatus.PASSED


# ---- 使用者的選擇優先 -----------------------------------------------------------------


def test_newest_generation_replaces_auto_choice(client, app):
    b, p, bg1, pose = produced(client, app)
    pid = p["id"]
    first = get_page(app, pid).selected_generation_id
    bg2 = gen_background(client, app, pid)
    pg = get_page(app, pid)
    assert pg.background_generation_id == bg2 and pg.auto_background_generation_id == bg2
    assert pg.status == PageStatus.DRAFT and pg.selected_generation_id is None  # 換了圖：舊合成圖不再選著
    drain(client, app)
    pg = get_page(app, pid)
    assert pg.status == PageStatus.PASSED and pg.selected_generation_id not in (None, first)


def test_user_selected_background_and_pose_are_never_replaced(client, app):
    b, p = book_page(client, app)
    pid = p["id"]
    bg1 = gen_background(client, app, pid)
    bg2 = gen_background(client, app, pid)
    pose1 = gen_pose(client, app, pid)
    pose2 = gen_pose(client, app, pid)
    assert get_page(app, pid).background_generation_id == bg2  # 自動選最新的
    picked(client, pid, "background", bg1)  # 使用者選了舊的
    picked(client, pid, "pose", pose1)
    drain(client, app)
    gen_background(client, app, pid)
    gen_pose(client, app, pid)
    pg = get_page(app, pid)
    assert pg.background_generation_id == bg1 and pg.pose_generation_id == pose1
    assert pg.auto_background_generation_id == bg2 and pg.auto_pose_generation_id == pose2
    drain(client, app)
    pg = get_page(app, pid)
    assert pg.background_generation_id == bg1 and pg.pose_generation_id == pose1
    params = json.loads(next(g for g in composites(app, pid) if g.id == pg.selected_generation_id).params_json)
    assert (params["background_generation_id"], params["pose_generation_id"]) == (bg1, pose1)
    assert pg.status == PageStatus.PASSED


def test_uploaded_background_is_a_user_choice(client, app):
    b, p = book_page(client, app)
    pid = p["id"]
    gen_background(client, app, pid)
    up = upload_bg(client, pid, png_bytes()).json()["background_generation_id"]
    gen_background(client, app, pid)  # 之後才完成的 AI 背景不能蓋掉上傳的圖
    gen_pose(client, app, pid)
    drain(client, app)
    pg = get_page(app, pid)
    assert pg.background_generation_id == up and pg.status == PageStatus.PASSED


def test_legacy_slots_without_auto_record_count_as_user_choice(client, app):
    """Phase 13 之前就選好的背景（auto 欄位是空的）：視為使用者選的，新的生成不取代。"""
    b, p = book_page(client, app)
    pid = p["id"]
    bg1 = gen_background(client, app, pid)
    set_page(app, pid, auto_background_generation_id=None)
    gen_background(client, app, pid)
    assert get_page(app, pid).background_generation_id == bg1


def test_discarded_generation_is_not_auto_selected(client, app):
    b, p = book_page(client, app)
    pid = p["id"]
    bg1 = gen_background(client, app, pid)
    bg2 = gen_background(client, app, pid, run=False)
    with app.state.sessions() as s:
        g = s.get(Generation, bg2)
        g.discarded = True
        s.add(g)
        s.commit()
    run_jobs(client, app)
    assert get_page(app, pid).background_generation_id == bg1


def test_pose_without_mask_is_not_auto_selected(client, app):
    b, p = book_page(client, app)
    app.state.engine.pose_mask = False
    gen_pose(client, app, p["id"])
    assert get_page(app, p["id"]).pose_generation_id is None


# ---- 失敗 → 請看一下 -------------------------------------------------------------------


def test_failed_page_generation_sets_attention(client, app):
    b, p = book_page(client, app)
    pid = p["id"]
    app.state.engine.outcomes.append(EngineError("ComfyUI 執行失敗：記憶體不足"))
    gid = gen_background(client, app, pid)
    pg = get_page(app, pid)
    assert pg.attention == "failed"
    with app.state.sessions() as s:
        err = s.get(Generation, gid).error
    assert err and pg.attention_detail == err
    assert queued(app, pid) == []  # 失敗不排頁面流程


def test_failed_cleanup_generation_does_not_set_attention(client, app):
    """背景清理失敗（按鈕的）：不設 hard attention；來源列被刪掉（source_generation_id 變 null）的也一樣。"""
    from app.services.gen_request import encode_request

    b, p, bg, pose = produced(client, app)
    pid = p["id"]
    assert get_page(app, pid).attention is None
    rj = encode_request(kind=GenKind.BACKGROUND, prompt="clean", negative_prompt="", seed=1, width=8, height=8,
                        extra={"cleanup": True})
    with app.state.sessions() as s:
        ids = []
        for source in (bg, None):
            g = Generation(book_id=b["id"], page_id=pid, kind=GenKind.BACKGROUND, user_text="", prompt="clean",
                           negative_prompt="", seed=1, width=8, height=8, request_json=rj,
                           status=GenStatus.FAILED, error="ComfyUI 執行失敗", source_generation_id=source)
            s.add(g)
            s.commit()
            ids.append(g.id)
    for gid in ids:
        with app.state.sessions() as s:
            pipeline.on_generation_failed(s, gid)
        assert get_page(app, pid).attention is None


def test_engine_unavailable_does_not_set_attention(client, app):
    b, p = book_page(client, app)
    app.state.engine.outcomes.append(EngineUnavailable("ComfyUI 沒有回應"))
    gen_background(client, app, p["id"])
    assert get_page(app, p["id"]).attention is None


def test_pipeline_failure_sets_attention_and_success_clears_it(client, app):
    b, p, bg, pose = produced(client, app)
    pid = p["id"]
    with app.state.sessions() as s:
        bg_key = s.get(Generation, bg).image_path
    data = app.state.storage.get(bg_key)
    app.state.storage.delete(bg_key)
    client.put(f"/api/pages/{pid}/placement", json={"cx": 0.3, "cy": 0.6, "height": 0.5, "flip": False})
    enqueue(app, pid)
    drain(client, app)
    pg = get_page(app, pid)
    assert pg.attention == "failed" and pg.attention_detail == "圖片檔案遺失，請重新產生"
    t = tasks(app, pid)[-1]
    assert t.status == PageTaskStatus.FAILED.value and t.error == "圖片檔案遺失，請重新產生"
    # 檔案回來後再跑一次：可以印，attention 清掉
    app.state.storage.put(bg_key, data)
    enqueue(app, pid)
    drain(client, app)
    pg = get_page(app, pid)
    assert pg.status == PageStatus.PASSED and pg.attention is None and pg.attention_detail is None


def test_unexpected_pipeline_error_is_reported_in_chinese(client, app, monkeypatch):
    b, p = book_page(client, app)
    pid = p["id"]
    gen_background(client, app, pid)
    gen_pose(client, app, pid)

    def boom(*a, **k):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(pipeline, "ensure_page_lineart", boom)
    drain(client, app)  # 不可丟出例外
    pg = get_page(app, pid)
    assert pg.attention == "failed" and pg.attention_detail.startswith("自動處理這一頁時發生錯誤")
    assert pg.status == PageStatus.DRAFT
    t = tasks(app, pid)[-1]
    assert t.status == PageTaskStatus.FAILED.value and "kaboom" in t.error


def test_deleted_page_task_fails_quietly(client, app):
    b, p = book_page(client, app)
    with app.state.sessions() as s:
        s.add(PageTask(page_id=p["id"] + 1000))
        s.commit()
    assert drain(client, app) == 1
    assert tasks(app)[-1].status == PageTaskStatus.FAILED.value


def test_retries_when_page_changes_mid_run(client, app, monkeypatch):
    b, p = book_page(client, app)
    pid = p["id"]
    gen_background(client, app, pid)
    gen_pose(client, app, pid)
    real = pipeline.ensure_page_lineart
    moved = {"cx": 0.45, "cy": 0.62, "height": 0.55, "flip": False}
    calls = []

    def racing(session, storage, page_id, **kw):
        if not calls:  # 第一次算線稿時，另一個分頁拖了角色
            calls.append(1)
            set_page(app, page_id, placement_json=json.dumps(moved))
            # 舊編輯器重新合成（選定圖換掉）
            assert client.post(f"/api/pages/{page_id}/compose").status_code == 200
        return real(session, storage, page_id, **kw)

    monkeypatch.setattr(pipeline, "ensure_page_lineart", racing)
    drain(client, app)
    pg = get_page(app, pid)
    assert pg.status == PageStatus.PASSED and pg.attention is None
    comp = next(g for g in composites(app, pid) if g.id == pg.selected_generation_id)
    assert json.loads(comp.params_json)["placement"] == moved


def _auto(app, page_id):
    pg = get_page(app, page_id)
    with app.state.sessions() as s:
        return load_strokes(s, pg.selected_generation_id, layer=StrokeLayer.AUTO)


def test_layout_change_mid_run_recomputes_auto_with_new_layout(client, app, monkeypatch):
    """流程算好自動補線後、存檔前改了列印方式：不能把舊版面的座標存下來（review 2026-11-13 bug 2）。"""
    b, p = book_page(client, app)
    pid = p["id"]
    gen_background(client, app, pid)
    gen_pose(client, app, pid)
    real = pipeline.plan_autofix
    calls = []

    def racing(*a, **kw):
        out = real(*a, **kw)
        if not calls:  # 第一次算完補線，另一個分頁改了列印方式
            calls.append(1)
            assert client.patch(f"/api/books/{b['id']}", json={"print_mode": "print_shop"}).status_code == 200
        return out

    monkeypatch.setattr(pipeline, "plan_autofix", racing)
    drain(client, app)
    pg = get_page(app, pid)
    assert pg.status == PageStatus.PASSED
    auto = _auto(app, pid)
    assert auto  # 有缺口：auto 層不是空的
    # 正確的 auto 層是新版面重算的結果：用真的 plan_autofix 再跑一次流程，結果不變
    monkeypatch.undo()
    assert enqueue(app, pid) is True
    drain(client, app)
    assert _auto(app, pid) == auto


def test_layout_change_before_pass_reruns(client, app, monkeypatch):
    """沒有缺口的頁（auto 層是空的、不會寫 auto 層）：算完缺口、標可以印之前改了列印方式，
    不能用舊版面標成可以印；重試後以新版面做完。"""
    b, p = book_page(client, app, gap=0)
    pid = p["id"]
    gen_background(client, app, pid)
    gen_pose(client, app, pid)
    real = pipeline.evaluate
    calls = []

    def racing(*a, **kw):  # 舊版面算完缺口（沒有缺口 → 不寫 auto 層）之後才改版面
        report = real(*a, **kw)
        if not calls:
            calls.append(1)
            assert client.patch(f"/api/books/{b['id']}", json={"print_mode": "print_shop"}).status_code == 200
        return report

    monkeypatch.setattr(pipeline, "evaluate", racing)
    real_layout = pipeline.book_layout_key
    seen = []

    def spy(session, page_id):
        key = real_layout(session, page_id)
        seen.append(key)
        return key

    monkeypatch.setattr(pipeline, "book_layout_key", spy)
    drain(client, app)
    assert get_page(app, pid).status == PageStatus.PASSED
    assert seen[-1] == ("a5", "print_shop") and len(seen) >= 2  # 重試了一次，用的是新版面


def test_layout_changed_back_is_accepted(client, app, monkeypatch):
    """計算中版面改了又改回來：計算時的版面＝寫入時的版面，照常存檔（不多重試一次）。"""
    b, p = book_page(client, app)
    pid = p["id"]
    gen_background(client, app, pid)
    gen_pose(client, app, pid)
    real = pipeline.plan_autofix
    calls = []

    def flipping(*a, **kw):
        calls.append(1)
        out = real(*a, **kw)
        if len(calls) == 1:
            for mode in ("print_shop", "home"):
                assert client.patch(f"/api/books/{b['id']}", json={"print_mode": mode}).status_code == 200
        return out

    monkeypatch.setattr(pipeline, "plan_autofix", flipping)
    drain(client, app)
    assert get_page(app, pid).status == PageStatus.PASSED
    assert len(calls) == 1 and _auto(app, pid)


def test_after_success_failure_still_enqueues_page(client, app, monkeypatch):
    """生成成功、但自動選圖／排流程丟例外：頁面仍要排進流程，不能停在「製作中」。"""
    b, p = book_page(client, app)
    pid = p["id"]

    def boom(session, gen_id):
        raise RuntimeError("資料庫暫時鎖住")

    monkeypatch.setattr(pipeline, "on_generation_succeeded", boom)
    gen_background(client, app, pid)
    gen_pose(client, app, pid)
    assert queued(app, pid)
    monkeypatch.undo()
    drain(client, app)
    assert get_page(app, pid).status == PageStatus.PASSED


def test_page_task_finish_failure_is_retried_next_loop(client, app, monkeypatch):
    """流程跑完但記結果一直失敗：下一輪迴圈補寫，不留 RUNNING 到重啟。"""
    b, p = book_page(client, app)
    pid = p["id"]
    gen_background(client, app, pid)
    gen_pose(client, app, pid)
    real = pipeline.finish_page_task
    fails = {"n": 0}

    def flaky(*a, **kw):
        if fails["n"] < 2:
            fails["n"] += 1
            raise RuntimeError("database is locked")
        return real(*a, **kw)

    monkeypatch.setattr(pipeline, "finish_page_task", flaky)
    drain(client, app)
    assert [t.status for t in tasks(app, pid)][-1] == PageTaskStatus.RUNNING.value
    client.portal.call(app.state.worker._retry_unfinished)
    assert [t.status for t in tasks(app, pid)][-1] == PageTaskStatus.SUCCEEDED.value


# ---- 排隊與重啟 ------------------------------------------------------------------------


def test_enqueue_is_idempotent_per_page(client, app):
    b, p = book_page(client, app)
    other = new_page(client, b["id"])
    assert enqueue(app, p["id"]) is True
    assert enqueue(app, p["id"]) is False
    assert enqueue(app, other["id"]) is True
    assert len(queued(app, p["id"])) == 1 and len(queued(app, other["id"])) == 1
    # 執行中的工作不擋新的排隊（執行中改了東西要再跑一次）
    with app.state.sessions() as s:
        t = s.exec(select(PageTask).where(PageTask.page_id == p["id"])).one()
        t.status = PageTaskStatus.RUNNING.value
        s.add(t)
        s.commit()
    assert enqueue(app, p["id"]) is True
    assert enqueue(app, p["id"]) is False
    assert [t.status for t in tasks(app, p["id"])] == ["running", "queued"]


def test_restart_requeues_running_tasks(client, app):
    b, p = book_page(client, app)
    other = new_page(client, b["id"])
    with app.state.sessions() as s:
        s.add(PageTask(page_id=p["id"], status=PageTaskStatus.RUNNING.value))
        s.add(PageTask(page_id=other["id"], status=PageTaskStatus.RUNNING.value))
        s.add(PageTask(page_id=other["id"]))  # 這頁已經有排隊中的：執行中那筆不再重排
        s.commit()
    assert app.state.worker.requeue_page_tasks() == 1
    assert [t.status for t in tasks(app, p["id"])] == ["queued"]
    assert sorted(t.status for t in tasks(app, other["id"])) == ["failed", "queued"]
    assert drain(client, app) == 2
    assert all(t.status != "queued" and t.status != "running" for t in tasks(app))


def test_worker_loop_runs_pipeline_between_generations(settings_file):
    """背景迴圈：每做完一張生成就先把排隊的頁面流程做完，再做下一張；重啟時執行中的工作重新排隊。"""
    app = create_app(load_settings(settings_file), engine=FakeEngine(), start_worker=False)
    with TestClient(app) as client:
        worker = app.state.worker
        b, p = book_page(client, app)
        pid = p["id"]
        seen = []
        engine = app.state.engine
        real = engine.generate

        async def spying(req, on_progress=None):
            seen.append((req.kind, get_page(app, pid).status))
            return await real(req, on_progress)

        engine.generate = spying
        client.post(f"/api/pages/{pid}/backgrounds")
        client.post(f"/api/pages/{pid}/poses", json={"action": "揮手"})
        other = client.post("/api/books", json={"title": "另一本", **A5_HOME}).json()
        r = client.post(f"/api/books/{other['id']}/character/candidates", json={"description": "小熊"})
        assert r.status_code == 202, r.text
        with app.state.sessions() as s:  # 上次執行到一半的頁面流程
            s.add(PageTask(page_id=pid, status=PageTaskStatus.RUNNING.value))
            s.commit()

        async def run():
            await worker.start()
            try:
                for _ in range(400):
                    if (get_page(app, pid).status == PageStatus.PASSED and len(seen) == 3
                            and not [t for t in tasks(app, pid) if t.status in ("queued", "running")]):
                        return
                    await asyncio.sleep(0.02)
                raise AssertionError(f"pipeline did not finish: {seen} {get_page(app, pid).status} {[(t.status, t.error) for t in tasks(app)]}")
            finally:
                await worker.stop()

        client.portal.call(run)
    assert [k for k, _ in seen] == [GenKind.BACKGROUND, GenKind.POSE, GenKind.CHARACTER]
    assert seen[2][1] == PageStatus.PASSED  # 角色圖開始前，這頁已經做好


def test_idle_loop_retries_unfinished_page_task(settings_file, monkeypatch):
    """沒有生成在跑（閒置）時，記結果一直失敗的頁面流程也會在退避後補寫，不等別的事件把迴圈叫醒。"""
    app = create_app(load_settings(settings_file), engine=FakeEngine(), start_worker=False)
    with TestClient(app) as client:
        worker = app.state.worker
        worker.retry_backoff_s = 0.05
        b, p = book_page(client, app)
        pid = p["id"]
        gen_background(client, app, pid)
        gen_pose(client, app, pid)
        real = pipeline.finish_page_task
        fails = {"n": 0}

        def flaky(*a, **kw):
            if fails["n"] < 2:
                fails["n"] += 1
                raise RuntimeError("database is locked")
            return real(*a, **kw)

        monkeypatch.setattr(pipeline, "finish_page_task", flaky)

        async def run():
            await worker.start()
            try:
                for _ in range(200):
                    if tasks(app, pid) and tasks(app, pid)[-1].status == PageTaskStatus.SUCCEEDED.value:
                        return
                    await asyncio.sleep(0.02)
                raise AssertionError([(t.status, t.error) for t in tasks(app, pid)])
            finally:
                await worker.stop()

        client.portal.call(run)


def test_page_tasks_run_while_engine_unavailable(settings_file):
    app = create_app(load_settings(settings_file), engine=FakeEngine(), start_worker=False)
    with TestClient(app) as client:
        worker = app.state.worker
        worker.retry_backoff_s = 0.05
        b, p, bg, pose = produced(client, app)
        pid = p["id"]
        set_page(app, pid, status=PageStatus.DRAFT, passed_at=None)
        app.state.engine.error = EngineUnavailable("down")
        client.post(f"/api/pages/{pid}/backgrounds")  # 這張會一直等引擎
        enqueue(app, pid)

        async def run():
            await worker.start()
            try:
                for _ in range(300):
                    if get_page(app, pid).status == PageStatus.PASSED:
                        return
                    await asyncio.sleep(0.02)
                raise AssertionError("page task blocked by engine outage")
            finally:
                await worker.stop()

        client.portal.call(run)


# ---- 舊編輯器仍是原本的行為 --------------------------------------------------------------


def test_old_editor_compose_still_resets_review_and_needs_check(client, app):
    b, p, bg, pose = produced(client, app)
    pid = p["id"]
    client.put(f"/api/pages/{pid}/placement", json={"cx": 0.3, "cy": 0.6, "height": 0.5, "flip": False})
    r = client.post(f"/api/pages/{pid}/compose")
    assert r.status_code == 200 and r.json()["status"] == "draft"
    r = client.post(f"/api/pages/{pid}/pass", json={})
    assert r.status_code == 409  # 舊編輯器仍要先按「對」
    gid = get_page(app, pid).selected_generation_id
    assert client.post(f"/api/pages/{pid}/character-check",
                       json={"generation_id": gid, "result": "consistent"}).status_code == 200
    assert client.post(f"/api/pages/{pid}/pass", json={}).json()["status"] == "passed"
    assert queued(app, pid) == []  # 舊編輯器的動作不排頁面流程


def test_delete_page_refused_while_pipeline_running(client, app):
    b, p = book_page(client, app)
    with app.state.sessions() as s:
        s.add(PageTask(page_id=p["id"], status=PageTaskStatus.RUNNING.value, updated_at=utcnow()))
        s.commit()
    r = client.delete(f"/api/pages/{p['id']}")
    assert r.status_code == 409
    assert get_page(app, p["id"]) is not None


def test_kept_changing_does_not_set_attention_when_another_task_is_queued(client, app, monkeypatch):
    """Task 2 review M1：一直被改而放棄（MAX_ATTEMPTS 次）時，這頁若已有排隊中的流程（改動的那一方排的），
    不設「請看一下」：下一個工作會用最新的內容再跑。沒有排隊中的工作時照常設 attention。"""
    b, p = book_page(client, app)
    pid = p["id"]

    def always_stale(*a, **k):
        raise pipeline._Stale()

    monkeypatch.setattr(pipeline, "_attempt", always_stale)
    assert enqueue(app, pid)
    with pytest.raises(pipeline.PipelineFailed) as e:
        run_pipeline(app, pid)
    assert e.value.message == pipeline.KEPT_CHANGING
    pg = get_page(app, pid)
    assert pg.attention is None and pg.attention_detail is None

    with app.state.sessions() as s:
        for t in s.exec(select(PageTask)).all():
            s.delete(t)
        s.commit()
    with pytest.raises(pipeline.PipelineFailed):
        run_pipeline(app, pid)
    pg = get_page(app, pid)
    assert pg.attention == "failed" and pg.attention_detail == pipeline.KEPT_CHANGING


def test_new_composite_sets_outline_fill(client, app):
    b, p, bg, pose = produced(client, app)
    pid = p["id"]
    set_page(app, pid, outline_fill=False)
    old = get_page(app, pid).selected_generation_id
    r = client.post(f"/api/pages/{pid}/poses", params={"oneclick": "true"}, json={"action": "jumping"})
    assert r.status_code == 202, r.text
    run_jobs(client, app)
    drain(client, app)
    pg = get_page(app, pid)
    assert pg.selected_generation_id != old
    assert pg.outline_fill is True


def test_reused_composite_keeps_outline_fill_and_key(client, app):
    b, p, bg, pose = produced(client, app)
    pid = p["id"]
    set_page(app, pid, outline_fill=False)
    assert run_pipeline(app, pid) == pipeline.PASSED  # 舊頁面（False）先用舊算法補一次線稿，記下它的 key
    before = get_page(app, pid)
    assert before.outline_fill is False
    assert enqueue(app, pid)
    drain(client, app)
    after = get_page(app, pid)
    assert after.selected_generation_id == before.selected_generation_id
    assert after.outline_fill is False
    assert after.lineart_key == before.lineart_key


def test_pipeline_keeps_review_pose(client, app):
    """頁面流程做完會清掉 failed，但保留軟標記 review_pose（批次重畫主角後換上的新動作要使用者看一下）。"""
    b, p, bg, pose = produced(client, app)
    pid = p["id"]
    set_page(app, pid, attention="review_pose", attention_detail="請看一下：換了新的主角動作")
    assert enqueue(app, pid)
    assert drain(client, app) == 1
    pg = get_page(app, pid)
    assert pg.status == PageStatus.PASSED
    assert pg.attention == "review_pose" and pg.attention_detail == "請看一下：換了新的主角動作"


def test_review_frame_is_soft():
    from app.services.page_state import hard_attention

    assert hard_attention("review_frame") is False
    assert hard_attention("review_pose") is False
    assert hard_attention("failed") is True and hard_attention(None) is False


def test_pipeline_flags_review_frame(client, app):
    """一鍵流程：肖像頁的背景找不到框 → 照一般合成、照樣可以印，但標軟標記 review_frame；換上有框的背景 → 清掉。"""
    from tests.test_page_compose import SEED, ring_png

    b, p, bg, pose = produced(client, app)  # 背景上緣有缺口：不是封閉的框
    pid = p["id"]
    r = client.put(f"/api/pages/{pid}/placement?oneclick=true",
                   json={"cx": 0.5, "cy": 0.66, "height": 0.6, "flip": False, "frame": SEED, "autofit": True})
    assert r.status_code == 200, r.text
    assert drain(client, app) == 1
    pg = get_page(app, pid)
    assert pg.status == PageStatus.PASSED
    assert pg.attention == "review_frame"
    assert pg.attention_detail == "這張背景找不到框，照一般擺放，可以再畫一張背景"
    ov = next(x for x in client.get(f"/api/books/{b['id']}/overview").json()["pages"] if x["id"] == pid)
    assert ov["state"] == "ready" and ov["review"] is True  # 軟標記：B 照樣算可以印，卡片顯示請看一下

    # 換一張有框的背景（oneclick 會排流程）
    r = client.post(f"/api/pages/{pid}/backgrounds/upload?oneclick=true",
                    files={"file": ("bg.png", ring_png(), "image/png")})
    assert r.status_code == 201, r.text
    assert drain(client, app) == 1
    pg = get_page(app, pid)
    assert pg.status == PageStatus.PASSED and pg.attention is None and pg.attention_detail is None
    assert json.loads(pg.placement_json)["height"] > 1.0  # autofit 寫回了框的肖像位置
