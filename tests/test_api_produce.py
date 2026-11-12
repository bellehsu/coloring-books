"""Phase 13 Task 3：「開始做這本書」（/produce、/produce/plan）與 B 整本書（/overview）。

- plan：AI 背景（沒選背景、沒上傳）與角色動作（有動作、沒選）各幾張、估計秒數、今天剩幾次、放得下幾張。
- produce：依頁面順序、每頁先背景再動作；只預留放得下的（其餘頁面延到明天），按兩次不重複，
  0 次時 429；admin 不設上限。已經有兩張圖但還沒做完的頁面排頁面流程（不計次數）。
- overview：每頁 working／attention／ready／todo、本輪進度、還有幾頁沒排。舊書照原樣顯示。
"""

import pytest
from fastapi.testclient import TestClient
from sqlmodel import col, select

from app.config import load_settings
from app.main import create_app
from app.models import Book, Generation, GenKind, GenStatus, Page, PageStatus, PageTask, PageTaskStatus
from app.services import usage
from app.services.engine import EngineError
from tests.fake_engine import FakeEngine
from tests.test_api_books import candidates, new_book, run_jobs
from tests.test_api_compose import upload_bg
from tests.test_api_lineart import A5_HOME, color_png, get_page, legacy_page, make_passed, set_page
from tests.test_api_pages import new_page
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


def book_with_pages(client, app, pages, **book_kw):
    """建書＋角色（用掉 1 次）＋頁面；pages 是每頁的 (scene, action)。回傳 (book, [page])。"""
    app.state.engine.image = color_png()
    b = new_book(client, **(book_kw or A5_HOME))
    gid = candidates(client, b["id"]).json()[0]["id"]
    run_jobs(client, app)
    assert client.post(f"/api/books/{b['id']}/character", json={"generation_id": gid}).status_code == 200
    ps = [new_page(client, b["id"], scene_prompt=s, action_prompt=a) for s, a in pages]
    return b, ps


def plan(client, book_id):
    r = client.get(f"/api/books/{book_id}/produce/plan")
    assert r.status_code == 200, r.text
    return r.json()


def produce(client, book_id, status=202):
    r = client.post(f"/api/books/{book_id}/produce")
    assert r.status_code == status, r.text
    return r.json()


def overview(client, book_id):
    r = client.get(f"/api/books/{book_id}/overview")
    assert r.status_code == 200, r.text
    return r.json()


def states(client, book_id) -> list[str]:
    return [p["state"] for p in overview(client, book_id)["pages"]]


def book_gens(app, book_id, *kinds) -> list[Generation]:
    kinds = kinds or (GenKind.BACKGROUND, GenKind.POSE)
    with app.state.sessions() as s:
        return list(s.exec(
            select(Generation).where(Generation.book_id == book_id, col(Generation.kind).in_(kinds))
            .order_by(col(Generation.id))
        ).all())


def page_tasks(app, page_id) -> list[PageTask]:
    with app.state.sessions() as s:
        return list(s.exec(select(PageTask).where(PageTask.page_id == page_id).order_by(col(PageTask.id))).all())


def drain(client, app) -> int:
    return client.portal.call(app.state.worker.run_page_tasks)


def run_all(client, app):
    """生成跑完（每張之後頁面流程也跑完），直到沒有排隊的工作。"""
    while client.portal.call(app.state.worker.run_once):
        pass
    drain(client, app)


THREE = [("公園", "揮手"), ("海邊", "跳"), ("森林", "跑")]


# ---- plan ---------------------------------------------------------------------------


def test_plan_counts_ai_pages_and_skips_uploaded_backgrounds(client, app):
    login(client, app, "管理者", is_admin=True)
    b, (p1, p2, p3, p4) = book_with_pages(
        client, app, [("公園", "揮手"), ("海邊", "跳"), ("森林", ""), ("", "跑")]
    )
    assert upload_bg(client, p2["id"], color_png()).status_code == 201  # 上傳背景：不排 AI 背景
    got = plan(client, b["id"])
    # 背景：p1、p3（p2 上傳了、p4 沒場景）；角色動作：p1、p2、p4（p3 沒動作）
    assert got == {"backgrounds": 2, "poses": 3, "total": 5, "estimate_s": 5 * 22,
                   "remaining": None, "fits": 5}


def test_plan_reports_remaining_and_fits_for_capped_owner(client, app):
    owner = login(client, app, cap=4)
    b, _ = book_with_pages(client, app, THREE)  # 角色用掉 1 次：剩 3
    got = plan(client, b["id"])
    assert got["total"] == 6 and got["remaining"] == 3 and got["fits"] == 3
    assert got["estimate_s"] == 3 * 22  # 這次按下去實際會做的
    assert used(app, owner) == 1  # plan 不計數


# ---- produce ------------------------------------------------------------------------


def test_partial_run_defers_the_rest_and_never_duplicates(client, app):
    owner = login(client, app, cap=4)
    b, (p1, p2, p3) = book_with_pages(client, app, THREE)  # 剩 3 次、要 6 次
    out = produce(client, b["id"])
    gens = book_gens(app, b["id"])
    assert out["queued"] == [g.id for g in gens]
    # 依頁面順序、每頁先背景再動作，放得下多少就排多少（p2 只排得下背景）
    assert [(g.page_id, g.kind) for g in gens] == [
        (p1["id"], GenKind.BACKGROUND), (p1["id"], GenKind.POSE), (p2["id"], GenKind.BACKGROUND)]
    assert all(g.status == GenStatus.QUEUED for g in gens)
    assert out["deferred_pages"] == [p2["id"], p3["id"]]
    assert used(app, owner) == 4
    ov = overview(client, b["id"])
    assert ov["deferred_count"] == 2
    assert [p["state"] for p in ov["pages"]] == ["working", "working", "todo"]

    # 用完了：429，什麼都不排
    r = client.post(f"/api/books/{b['id']}/produce")
    assert r.status_code == 429 and r.json()["detail"] == CAP_MSG
    assert len(book_gens(app, b["id"])) == 3 and used(app, owner) == 4

    # 隔天（這裡用提高上限代替）：只補還沒排的，排隊中的不重複
    set_cap(app, owner, 20)
    assert plan(client, b["id"])["total"] == 3
    out = produce(client, b["id"])
    gens = book_gens(app, b["id"])
    assert [(g.page_id, g.kind) for g in gens[3:]] == [
        (p2["id"], GenKind.POSE), (p3["id"], GenKind.BACKGROUND), (p3["id"], GenKind.POSE)]
    assert out["queued"] == [g.id for g in gens[3:]] and out["deferred_pages"] == []
    assert used(app, owner) == 7

    # 再按一次：沒有東西要排，不計數也不 429
    out = produce(client, b["id"])
    assert out == {"queued": [], "deferred_pages": [], "pipeline_pages": []}
    assert len(book_gens(app, b["id"])) == 6 and used(app, owner) == 7
    assert overview(client, b["id"])["deferred_count"] == 0


def test_running_job_is_not_duplicated(client, app):
    login(client, app, cap=60)
    b, (p1,) = book_with_pages(client, app, [("公園", "揮手")])
    produce(client, b["id"])
    with app.state.sessions() as s:
        g = s.exec(select(Generation).where(Generation.page_id == p1["id"],
                                            Generation.kind == GenKind.BACKGROUND)).one()
        g.status = GenStatus.RUNNING
        s.add(g)
        s.commit()
    assert produce(client, b["id"])["queued"] == []
    assert len(book_gens(app, b["id"])) == 2


def test_zero_remaining_is_429(client, app):
    owner = login(client, app, cap=1)
    b, _ = book_with_pages(client, app, [("公園", "揮手")])  # 角色用掉唯一的 1 次
    p = plan(client, b["id"])
    assert p["remaining"] == 0 and p["fits"] == 0 and p["total"] == 2 and p["estimate_s"] == 0
    r = client.post(f"/api/books/{b['id']}/produce")
    assert r.status_code == 429 and r.json()["detail"] == CAP_MSG
    assert book_gens(app, b["id"]) == [] and used(app, owner) == 1


def test_admin_is_uncapped(client, app):
    owner = login(client, app, "管理者", cap=1, is_admin=True)
    b, _ = book_with_pages(client, app, THREE)
    assert plan(client, b["id"])["remaining"] is None
    out = produce(client, b["id"])
    assert len(out["queued"]) == 6 and out["deferred_pages"] == []
    assert used(app, owner) == 7  # 照常計數


def test_poses_wait_for_a_character(client, app):
    login(client, app, cap=60)
    b = new_book(client, **A5_HOME)
    new_page(client, b["id"], scene_prompt="公園", action_prompt="揮手")
    got = plan(client, b["id"])
    assert got["backgrounds"] == 1 and got["poses"] == 0
    assert len(produce(client, b["id"])["queued"]) == 1


def test_produce_finishes_pages_that_already_have_both_images(client, app):
    """已經有背景與角色動作、還沒做完、也沒有排隊中的流程：排頁面流程（不計次數），按兩次不重複。"""
    owner = login(client, app, cap=60)
    b, (p1,) = book_with_pages(client, app, [("公園", "揮手")])
    produce(client, b["id"])
    while client.portal.call(app.state.worker.run_once):
        pass
    # 流程還沒跑就被清掉（例如舊版本留下的頁面）：頁面是「還沒做完」
    with app.state.sessions() as s:
        for t in s.exec(select(PageTask)).all():
            s.delete(t)
        s.commit()
    assert states(client, b["id"]) == ["todo"]
    before = used(app, owner)
    out = produce(client, b["id"])
    assert out == {"queued": [], "deferred_pages": [], "pipeline_pages": [p1["id"]]}
    assert [t.status for t in page_tasks(app, p1["id"])] == [PageTaskStatus.QUEUED.value]
    assert produce(client, b["id"])["pipeline_pages"] == []  # 已經排了
    assert len(page_tasks(app, p1["id"])) == 1 and used(app, owner) == before
    assert states(client, b["id"]) == ["working"]
    drain(client, app)
    assert states(client, b["id"]) == ["ready"]
    assert produce(client, b["id"])["pipeline_pages"] == []  # 可以印了：不再排


def test_pipeline_pages_are_queued_even_when_the_cap_is_used_up(client, app):
    owner = login(client, app, cap=3)
    b, (p1, p2) = book_with_pages(client, app, [("公園", "揮手"), ("海邊", "跳")])
    produce(client, b["id"])  # 剩 2 次：p1 的兩張
    while client.portal.call(app.state.worker.run_once):
        pass
    with app.state.sessions() as s:
        for t in s.exec(select(PageTask)).all():
            s.delete(t)
        s.commit()
    out = produce(client, b["id"])  # 0 次：p2 延後，但 p1 的流程照排
    assert out == {"queued": [], "deferred_pages": [p2["id"]], "pipeline_pages": [p1["id"]]}
    assert used(app, owner) == 3


def test_produce_retries_a_failed_generation(client, app):
    login(client, app, cap=60)
    b, (p1,) = book_with_pages(client, app, [("公園", "揮手")])
    app.state.engine.outcomes.append(EngineError("壞掉了"))
    produce(client, b["id"])
    run_jobs(client, app)  # 背景失敗
    ov = overview(client, b["id"])
    assert ov["pages"][0]["state"] == "working"  # 角色動作還在排隊
    run_jobs(client, app)
    drain(client, app)  # 角色動作好了：流程等背景（不清掉 attention）
    ov = overview(client, b["id"])
    assert ov["pages"][0]["state"] == "attention" and ov["pages"][0]["attention_detail"]
    out = produce(client, b["id"])  # 繼續做：失敗的背景重排一張
    [g] = [x for x in book_gens(app, b["id"]) if x.id in out["queued"]]
    assert g.kind == GenKind.BACKGROUND
    assert states(client, b["id"]) == ["working"]


# ---- overview ---------------------------------------------------------------------------


def test_overview_new_book_through_a_full_run(client, app):
    login(client, app, cap=60)
    b, (p1, p2) = book_with_pages(client, app, [("公園", "揮手"), ("海邊", "跳")])
    set_page(app, p1["id"], caption_zh="公園", caption_en="park")
    ov = overview(client, b["id"])
    assert ov["progress"] == {"queued": 0, "running": 0, "done": 0}
    assert ov["run_started_at"] is None and ov["estimate_s"] == 0
    assert ov["deferred_count"] == 2  # 還沒排的頁數（還沒開始做也一樣）
    first = ov["pages"][0]
    assert first == {"id": p1["id"], "position": p1["position"], "state": "todo", "gap_count": None,
                     "caption_zh": "公園", "caption_en": "park", "caption_show_zh": True, "caption_show_en": True,
                     "thumb_url": None,
                     "lineart_url": None, "attention_detail": None}

    produce(client, b["id"])
    ov = overview(client, b["id"])
    assert ov["progress"] == {"queued": 4, "running": 0, "done": 0}
    assert ov["estimate_s"] == 4 * 22 and ov["run_started_at"] is not None
    assert ov["deferred_count"] == 0
    run_jobs(client, app)  # p1 背景好了：排一個頁面流程
    ov = overview(client, b["id"])
    assert ov["progress"] == {"queued": 4, "running": 0, "done": 1}
    assert ov["estimate_s"] == 3 * 22  # 只算生成（頁面流程很快）

    run_all(client, app)
    ov = overview(client, b["id"])
    assert ov["progress"] == {"queued": 0, "running": 0, "done": 6}  # 4 張生成＋2 個頁面流程
    assert [p["state"] for p in ov["pages"]] == ["ready", "ready"]
    pg = get_page(app, p1["id"])
    first = ov["pages"][0]
    assert first["thumb_url"] == f"/api/generations/{pg.selected_generation_id}/image"
    assert first["lineart_url"] == f"/api/pages/{p1['id']}/lineart?v={pg.lineart_key}"
    assert first["gap_count"] == 0

    # 下一輪：新的開始時間，上一輪做完的不再算
    new_page(client, b["id"], scene_prompt="森林", action_prompt="跑")
    produce(client, b["id"])
    ov = overview(client, b["id"])
    assert ov["progress"] == {"queued": 2, "running": 0, "done": 0}
    assert [p["state"] for p in ov["pages"]] == ["ready", "ready", "working"]


def test_overview_ignores_old_finished_tasks(client, app):
    from sqlalchemy import event
    from datetime import timedelta

    login(client, app, cap=60)
    b, (p1, p2) = book_with_pages(client, app, [("公園", "揮手"), ("海邊", "跳")])
    produce(client, b["id"])
    run_all(client, app)
    before = overview(client, b["id"])
    started = before["run_started_at"]
    assert before["progress"]["done"] == 6
    with app.state.sessions() as s:  # 上一輪留下的大量已完成工作
        book = s.get(Book, b["id"])
        old = book.produce_started_at - timedelta(days=1)
        for pid in (p1["id"], p2["id"]):
            for _ in range(20):
                s.add(PageTask(page_id=pid, status=PageTaskStatus.SUCCEEDED.value, created_at=old))
        s.commit()
    seen: list[str] = []

    def spy(conn, cursor, statement, *a):
        if "FROM page_task" in statement:
            seen.append(statement)

    event.listen(app.state.db, "before_cursor_execute", spy)
    try:
        after = overview(client, b["id"])
    finally:
        event.remove(app.state.db, "before_cursor_execute", spy)
    assert after == before and after["run_started_at"] == started
    assert seen and all("created_at" in q.split("WHERE", 1)[1] for q in seen)  # 在 SQL 就篩掉舊的，不是載入後才過濾


def test_pressing_again_while_running_keeps_the_run(client, app):
    login(client, app, cap=60)
    b, (p1, p2) = book_with_pages(client, app, [("公園", "揮手"), ("海邊", "跳")])
    produce(client, b["id"])
    run_jobs(client, app)
    started = overview(client, b["id"])["run_started_at"]
    new_page(client, b["id"], scene_prompt="森林", action_prompt="跑")
    produce(client, b["id"])  # 還有工作在跑：同一輪，做完的照算
    ov = overview(client, b["id"])
    assert ov["run_started_at"] == started
    assert ov["progress"]["done"] == 1 and ov["progress"]["queued"] == 6


def test_overview_attention_and_ready_from_page_fields(client, app):
    login(client, app, cap=60)
    b, (p1, p2, p3) = book_with_pages(client, app, THREE)
    set_page(app, p1["id"], attention="failed", attention_detail="圖片產生失敗，請重新產生")
    make_passed(app, p2["id"])
    ov = overview(client, b["id"])
    assert [p["state"] for p in ov["pages"]] == ["attention", "ready", "todo"]
    assert ov["pages"][0]["attention_detail"] == "圖片產生失敗，請重新產生"
    assert ov["pages"][1]["attention_detail"] is None
    # 首頁書卡用的統計與 B 的狀態一致（線沒接起來不算「請看一下」）
    stats = client.get(f"/api/books/{b['id']}").json()["stats"]
    assert {k: stats[k] for k in ("ready", "attention", "working", "todo")} == \
        {"ready": 1, "attention": 1, "working": 0, "todo": 1}
    # 有排隊中的工作時優先顯示製作中
    with app.state.sessions() as s:
        s.add(PageTask(page_id=p1["id"]))
        s.commit()
    assert states(client, b["id"])[0] == "working"
    listed = {x["id"]: x for x in client.get("/api/books").json()}
    stats = listed[b["id"]]["stats"]
    assert {k: stats[k] for k in ("ready", "attention", "working", "todo")} == \
        {"ready": 1, "attention": 0, "working": 1, "todo": 1}


def test_overview_and_produce_on_a_legacy_book(client, app):
    """舊書：PASSED 是可以印、其他是還沒做完；繼續做不動舊流程的整頁圖。"""
    login(client, app, cap=60)
    b, p, [gid] = legacy_page(client, app, scene_prompt="公園", action_prompt="揮手")
    b2, q, [gid2] = legacy_page(client, app, scene_prompt="海邊", action_prompt="跳")
    make_passed(app, p["id"], lineart_key=None)
    ov = overview(client, b["id"])
    assert [x["state"] for x in ov["pages"]] == ["ready"]
    assert ov["pages"][0]["thumb_url"] == f"/api/generations/{gid}/image"
    assert ov["pages"][0]["gap_count"] is None  # 線稿還沒算過：GET 不算線稿
    assert ov["pages"][0]["lineart_url"] is not None
    assert ov["deferred_count"] == 0

    # 另一本舊書（第二個 legacy_page 建了新書）：DRAFT 的整頁圖頁面是還沒做完，繼續做不排任何東西
    book2 = b2["id"]
    assert states(client, book2) == ["todo"]
    before = get_page(app, q["id"])
    assert produce(client, book2) == {"queued": [], "deferred_pages": [], "pipeline_pages": []}
    after = get_page(app, q["id"])
    assert after.selected_generation_id == before.selected_generation_id == gid2
    assert after.status == PageStatus.DRAFT
    assert book_gens(app, book2) == [] and page_tasks(app, q["id"]) == []


def test_overview_gap_count_comes_from_cached_qa(client, app):
    login(client, app, cap=60)
    b, (p1,) = book_with_pages(client, app, [("公園", "揮手")])
    produce(client, b["id"])
    run_all(client, app)
    page = get_page(app, p1["id"])
    assert page.qa_json
    # 線稿參數改了（快取的 QA 不是目前的線稿）：不在 GET 重算，回 None
    set_page(app, p1["id"], line_width_px=page.line_width_px + 2)
    assert overview(client, b["id"])["pages"][0]["gap_count"] is None


def test_produce_overview_and_plan_404_for_other_owner(client, app):
    login(client, app, "甲", cap=60)
    b, _ = book_with_pages(client, app, [("公園", "揮手")])
    login(client, app, "乙", cap=60)
    for method, url in (("GET", f"/api/books/{b['id']}/produce/plan"),
                        ("POST", f"/api/books/{b['id']}/produce"),
                        ("GET", f"/api/books/{b['id']}/overview")):
        r = client.request(method, url)
        assert r.status_code == 404 and r.json()["detail"] == "找不到這本繪本", url
    assert book_gens(app, b["id"]) == []
    with app.state.sessions() as s:
        assert s.get(Book, b["id"]).produce_started_at is None


def test_page_retry_counts_against_the_cap(client, app):
    """C 的「再試一次」重排失敗的那一張：算 1 次；用完了 429，不排。"""
    from tests.test_pipeline import drain

    owner = login(client, app, cap=4)
    b, (p,) = book_with_pages(client, app, [("公園", "揮手")])  # 角色 1 次（失敗的生成會退回次數）
    pid = p["id"]
    assert client.post(f"/api/pages/{pid}/backgrounds", params={"oneclick": "true"}).status_code == 202
    assert client.post(f"/api/pages/{pid}/poses", params={"oneclick": "true"}, json={"action": "揮手"}).status_code == 202
    run_jobs(client, app, 2)
    drain(client, app)
    app.state.engine.outcomes.append(EngineError("ComfyUI 執行失敗"))
    assert client.post(f"/api/pages/{pid}/poses", params={"oneclick": "true"}, json={"action": "跳"}).status_code == 202
    run_jobs(client, app)
    assert used(app, owner) == 3 and get_page(app, pid).attention == "failed"
    set_cap(app, owner, 3)
    r = client.post(f"/api/pages/{pid}/retry")
    assert r.status_code == 429 and r.json()["detail"] == CAP_MSG
    set_cap(app, owner, 4)
    r = client.post(f"/api/pages/{pid}/retry")
    assert r.status_code == 200 and r.json()["requeued"] == ["pose"]
    assert used(app, owner) == 4
