"""重畫主角（fix round 2026-11-13 #4）：B 的批次重畫（每頁 take＋候選、review_pose）與 C 的「一次畫 N 張」。

第 1 張是 take（好了自動換上去、有復原快照），第 2、3 張是候選（candidate）：自動選圖、頁面流程、
「還原成自動畫的那張」都不會拿它們。批次的 take 另帶 review：換上去時頁面標成 review_pose。
"""

import json

import pytest
from fastapi.testclient import TestClient
from sqlmodel import col, select

from app.config import load_settings
from app.main import create_app
from app.models import Generation, GenKind, GenStatus, Owner, PageStatus, PageTask, PageTaskStatus, Usage
from app.services import auth, pipeline, usage
from app.services.engine import EngineError
from app.services.jobs import BookState
from app.services.page_state import ATTENTION_REVIEW_POSE, REVIEW_POSE_DETAIL
from app.services.prompts import QWEN_POSE_VARIATIONS
from app.services.slots import newest_usable_ai
from tests.fake_engine import FakeEngine
from tests.test_api_books import new_book, run_jobs
from tests.test_api_compose import gen_background, gen_pose, picked
from tests.test_api_lineart import get_page, set_page
from tests.test_api_pages import new_page
from tests.test_pipeline import book_page, drain


@pytest.fixture
def app(settings_file):
    return create_app(load_settings(settings_file), engine=FakeEngine(), start_worker=False)


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


# ---- 小工具 ------------------------------------------------------------------------


def composed_pages(client, app, n=2):
    """建書＋角色＋n 頁（每頁有動作描述），背景與角色動作都產生好、頁面流程跑完（PASSED）。
    回傳 (book, [page dict], {page_id: 原本的角色動作 id})。"""
    b, p = book_page(client, app)
    pages = [p] + [new_page(client, b["id"], scene_prompt="海邊", action_prompt="跳") for _ in range(n - 1)]
    poses = {}
    for pg in pages:
        gen_background(client, app, pg["id"])
        poses[pg["id"]] = gen_pose(client, app, pg["id"])
    drain(client, app)
    for pg in pages:
        page = get_page(app, pg["id"])
        assert page.status == PageStatus.PASSED and page.pose_generation_id == poses[pg["id"]]
    return b, pages, poses


def redraw(client, book_id, page_ids, count=1):
    return client.post(f"/api/books/{book_id}/poses/redraw", json={"page_ids": page_ids, "count": count})


def rj(app, gen_id) -> dict:
    with app.state.sessions() as s:
        return json.loads(s.get(Generation, gen_id).request_json)


def gen(app, gen_id) -> Generation:
    with app.state.sessions() as s:
        return s.get(Generation, gen_id)


def all_gens(app, **where) -> list[Generation]:
    with app.state.sessions() as s:
        q = select(Generation).order_by(col(Generation.id))
        for k, v in where.items():
            q = q.where(getattr(Generation, k) == v)
        return list(s.exec(q).all())


def queued_tasks(app, page_id=None) -> list[PageTask]:
    with app.state.sessions() as s:
        q = select(PageTask).where(col(PageTask.status) == PageTaskStatus.QUEUED.value)
        if page_id is not None:
            q = q.where(col(PageTask.page_id) == page_id)
        return list(s.exec(q).all())


def run_all(client, app):
    """跑完所有排隊中的生成，再跑完頁面流程。"""
    n = len(all_gens(app, status=GenStatus.QUEUED))
    run_jobs(client, app, n)
    drain(client, app)


# ---- 批次 ---------------------------------------------------------------------------


def test_batch_redraw_queues_take_plus_candidates(client, app):
    b, pages, _ = composed_pages(client, app)
    ids = [p["id"] for p in pages]
    r = redraw(client, b["id"], ids, count=3)
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["skipped"] == []
    queued = body["queued"]
    assert len(queued) == 6
    assert [g["page_id"] for g in queued] == [ids[0]] * 3 + [ids[1]] * 3  # 依頁面順序，每頁 take 在前
    for pid in ids:
        mine = [g["id"] for g in queued if g["page_id"] == pid]
        first, *rest = (rj(app, g) for g in mine)
        assert first.get("take") is True and first.get("review") is True and "candidate" not in first
        for d in rest:
            assert d.get("candidate") is True and "take" not in d and "review" not in d
        variations = [d["variation"] for d in (first, *rest)]
        assert len(set(variations)) == 3, variations  # 同一頁的三張構圖都不同
        for g in mine:
            row = gen(app, g)
            assert row.kind == GenKind.POSE and row.status == GenStatus.QUEUED
            assert row.user_text == get_page(app, pid).action_prompt  # 用頁面目前的動作描述


def test_batch_redraw_over_cap_is_429_and_queues_nothing(client, app):
    """Review Focus：超過今天剩下的額度 → 整批 429（既有訊息），一筆都不排、額度不動、頁面不標記。"""
    with app.state.sessions() as s:
        owner, token = auth.create_owner(s, "甲", cap=60)
    client.cookies.clear()
    assert client.get(f"/i/{token}", follow_redirects=False).status_code == 303
    b, pages, _ = composed_pages(client, app)
    with app.state.sessions() as s:
        o = s.get(Owner, owner.id)
        used = 60 - usage.remaining(s, o, usage.clock())
        o.daily_generation_cap = used + 5
        s.add(o)
        s.commit()
    before = [(g.id, g.status) for g in all_gens(app)]
    pages_before = [(get_page(app, p["id"]).attention, get_page(app, p["id"]).pose_generation_id) for p in pages]

    r = redraw(client, b["id"], [p["id"] for p in pages], count=3)
    assert r.status_code == 429
    assert r.json()["detail"] == "今天只剩 5 次，這次需要 6 次"
    assert [(g.id, g.status) for g in all_gens(app)] == before
    with app.state.sessions() as s:
        rows = s.exec(select(Usage).where(col(Usage.owner_id) == owner.id)).all()
        assert sum(u.generations for u in rows) == used
    assert [(get_page(app, p["id"]).attention, get_page(app, p["id"]).pose_generation_id)
            for p in pages] == pages_before
    assert queued_tasks(app) == []


def test_batch_redraw_end_to_end(client, app):
    b, pages, poses = composed_pages(client, app)
    ids = [p["id"] for p in pages]
    r = redraw(client, b["id"], ids, count=2)
    assert r.status_code == 202, r.text
    queued = r.json()["queued"]
    run_all(client, app)

    ov = {p["id"]: p for p in client.get(f"/api/books/{b['id']}/overview").json()["pages"]}
    for pid in ids:
        take_id, cand_id = [g["id"] for g in queued if g["page_id"] == pid]
        page = get_page(app, pid)
        assert page.pose_generation_id == take_id
        assert page.status == PageStatus.PASSED
        assert page.attention == ATTENTION_REVIEW_POSE and page.attention_detail == REVIEW_POSE_DETAIL
        assert ov[pid]["state"] == "ready" and ov[pid]["review"] is True
        assert ov[pid]["attention_detail"] == REVIEW_POSE_DETAIL
        cand = gen(app, cand_id)
        assert cand.status == GenStatus.SUCCEEDED and not cand.discarded
        assert cand.id not in (page.pose_generation_id, page.auto_pose_generation_id)

    # 換掉的動作回得來：↶ 復原 → 原本的動作
    pid = ids[0]
    r = client.post(f"/api/pages/{pid}/undo")
    assert r.status_code == 200, r.text
    assert get_page(app, pid).pose_generation_id == poses[pid]


def test_two_redraws_keep_every_pose(client, app):
    """Review Focus：同一頁連按兩次重畫（第一次還沒好）：兩張都依序換上去，最後是第二張；
    每個動作都留在紀錄裡，復原一次退一步。"""
    b, pages, poses = composed_pages(client, app, n=1)
    pid = pages[0]["id"]
    r1 = redraw(client, b["id"], [pid])
    r2 = redraw(client, b["id"], [pid])
    assert r1.status_code == 202 and r2.status_code == 202
    t1, t2 = r1.json()["queued"][0]["id"], r2.json()["queued"][0]["id"]
    run_all(client, app)

    page = get_page(app, pid)
    assert page.pose_generation_id == t2 and page.status == PageStatus.PASSED
    assert page.attention == ATTENTION_REVIEW_POSE
    listed = client.get(f"/api/books/{b['id']}/generations", params={"kind": "pose", "page_id": pid}).json()
    assert [g["id"] for g in listed] == [t2, t1, poses[pid]]
    assert all(g["status"] == "succeeded" for g in listed)

    assert client.post(f"/api/pages/{pid}/undo").status_code == 200
    assert get_page(app, pid).pose_generation_id == t1
    assert client.post(f"/api/pages/{pid}/undo").status_code == 200
    assert get_page(app, pid).pose_generation_id == poses[pid]


def test_candidate_success_selects_nothing(client, app):
    b, pages, _ = composed_pages(client, app, n=1)
    pid = pages[0]["id"]
    take_id, cand_id = [g["id"] for g in redraw(client, b["id"], [pid], count=2).json()["queued"]]
    run_jobs(client, app)  # take
    drain(client, app)
    page = get_page(app, pid)
    assert page.pose_generation_id == take_id
    slots = (page.pose_generation_id, page.auto_pose_generation_id, page.selected_generation_id,
             page.background_generation_id, page.attention)

    run_jobs(client, app)  # 候選：worker 成功後什麼都不選、不排頁面流程
    assert gen(app, cand_id).status == GenStatus.SUCCEEDED
    assert queued_tasks(app, pid) == []
    with app.state.sessions() as s:
        assert pipeline.on_generation_succeeded(s, cand_id) is False
    assert queued_tasks(app, pid) == []
    page = get_page(app, pid)
    assert (page.pose_generation_id, page.auto_pose_generation_id, page.selected_generation_id,
            page.background_generation_id, page.attention) == slots


def test_candidates_never_auto_selected_or_restored(client, app):
    b, pages, poses = composed_pages(client, app, n=1)
    pid = pages[0]["id"]
    take_id, cand_id = [g["id"] for g in redraw(client, b["id"], [pid], count=2).json()["queued"]]
    run_all(client, app)
    assert cand_id > take_id
    with app.state.sessions() as s:
        assert newest_usable_ai(s, pid, GenKind.POSE) == take_id  # 候選比較新，但不是自動選圖的候選

    # 使用者自己選回原本的動作，auto_* 也不在了：「還原成自動畫的那張」找最新的 AI 圖 → take，不是候選
    picked(client, pid, "pose", poses[pid])
    set_page(app, pid, auto_pose_generation_id=None)
    r = client.post(f"/api/pages/{pid}/restore", json={"item": "pose"})
    assert r.status_code == 200, r.text
    assert get_page(app, pid).pose_generation_id == take_id

    # 批次判斷（產生全部素材、繼續做）：只有候選的角色動作不算「有圖」
    with app.state.sessions() as s:
        state = BookState(s, b["id"])
        page = next(p for p in state.pages if p.id == pid)
        page.pose_generation_id = None
        state.gens[GenKind.POSE][pid] = [s.get(Generation, cand_id)]
        assert state.has_both_images(page) is False
        state.gens[GenKind.POSE][pid] = [s.get(Generation, take_id)]
        assert state.has_both_images(page) is True
        s.rollback()


def test_batch_redraw_validation(client, app):
    b, pages, _ = composed_pages(client, app, n=1)
    pid = pages[0]["id"]
    for count in (0, 4):
        assert redraw(client, b["id"], [pid], count=count).status_code == 422
    assert client.post(f"/api/books/{b['id']}/poses/redraw", json={"page_ids": []}).status_code == 422

    # 別本書的頁 → 400，什麼都不排
    other = new_book(client)
    foreign = new_page(client, other["id"], scene_prompt="森林", action_prompt="跑")
    n_before = len(all_gens(app))
    r = redraw(client, b["id"], [pid, foreign["id"]])
    assert r.status_code == 400 and r.json()["detail"] == "page_ids 必須是這本繪本的頁面"
    assert redraw(client, b["id"], [987654]).status_code == 400
    assert len(all_gens(app)) == n_before

    # 沒有角色 → 409
    r = redraw(client, other["id"], [foreign["id"]])
    assert r.status_code == 409 and r.json()["detail"] == "請先設定角色"

    # 沒有動作描述的頁略過；重複的 id 只排一次、保留順序
    blank = new_page(client, b["id"], scene_prompt="河邊", action_prompt="")
    r = redraw(client, b["id"], [blank["id"], pid, pid])
    assert r.status_code == 202, r.text
    body = r.json()
    assert [g["page_id"] for g in body["queued"]] == [pid]
    assert body["skipped"] == [
        {"kind": "pose", "page_id": blank["id"], "position": blank["position"], "reason": "沒有動作描述"}
    ]


# ---- C 的「一次畫 N 張」 -------------------------------------------------------------


def test_inline_count_two(client, app):
    b, pages, _ = composed_pages(client, app, n=1)
    pid = pages[0]["id"]
    before = {g.id for g in all_gens(app)}
    r = client.post(f"/api/pages/{pid}/poses", params={"oneclick": "true"}, json={"action": "跳舞", "count": 2})
    assert r.status_code == 202, r.text
    new = [g for g in all_gens(app) if g.id not in before]
    assert len(new) == 2 and r.json()["id"] == new[0].id
    take, cand = (rj(app, g.id) for g in new)
    assert take.get("take") is True and "review" not in take and "candidate" not in take
    assert cand.get("candidate") is True and "take" not in cand
    assert take["variation"] != cand["variation"]
    assert all(g.user_text == "跳舞" for g in new)

    run_all(client, app)
    page = get_page(app, pid)
    assert page.attention is None
    assert page.pose_generation_id == new[0].id and page.status == PageStatus.PASSED

    # count 省略 = 1：跟以前的「換一張」一樣只排一張 take
    before = {g.id for g in all_gens(app)}
    r = client.post(f"/api/pages/{pid}/poses", params={"oneclick": "true"}, json={"action": "跳舞"})
    assert r.status_code == 202
    new = [g for g in all_gens(app) if g.id not in before]
    assert len(new) == 1 and rj(app, new[0].id).get("take") is True and "candidate" not in rj(app, new[0].id)
    for count in (0, 4):
        r = client.post(f"/api/pages/{pid}/poses", json={"action": "跳舞", "count": count})
        assert r.status_code == 422


def test_not_candidate_tolerates_malformed_request_json(client, app):
    """壞掉的 request_json 不會讓自動選圖丟錯，也不當成候選。"""
    _b, pages, poses = composed_pages(client, app, n=1)
    pid = pages[0]["id"]
    with app.state.sessions() as s:
        g = s.get(Generation, poses[pid])
        g.request_json = "{not json"
        s.add(g)
        s.commit()
        assert newest_usable_ai(s, pid, GenKind.POSE) == poses[pid]
        assert pipeline.is_candidate(s.get(Generation, poses[pid])) is False


# ---- fix round 1：候選失敗不影響頁面、候選成功不遮住失敗的 take、面向語、inline take 清掉 review_pose ----


def test_failed_candidate_keeps_page_ready(client, app):
    """候選（第 2 張）失敗：頁面照樣是 review_pose、可以印；「再試一次」不會排新的角色動作（不會換掉好的那張）。"""
    b, pages, _ = composed_pages(client, app, n=1)
    pid = pages[0]["id"]
    take_id, cand_id = [g["id"] for g in redraw(client, b["id"], [pid], count=2).json()["queued"]]
    run_jobs(client, app)  # take：換上去、頁面流程跑完
    drain(client, app)
    app.state.engine.outcomes.append(EngineError("ComfyUI 執行失敗：記憶體不足"))
    run_jobs(client, app)  # 候選失敗（頁面已經做好）
    drain(client, app)
    assert gen(app, take_id).status == GenStatus.SUCCEEDED
    assert gen(app, cand_id).status == GenStatus.FAILED
    page = get_page(app, pid)
    assert page.pose_generation_id == take_id
    assert page.attention == ATTENTION_REVIEW_POSE and page.attention_detail == REVIEW_POSE_DETAIL
    ov = {p["id"]: p for p in client.get(f"/api/books/{b['id']}/overview").json()["pages"]}
    assert ov[pid]["state"] == "ready" and ov[pid]["review"] is True

    n_before = len(all_gens(app))
    r = client.post(f"/api/pages/{pid}/retry")
    assert r.status_code == 200, r.text
    assert r.json()["requeued"] == [] and r.json()["pipeline"] is True
    assert len(all_gens(app)) == n_before  # 沒有排新的（要算額度的）角色動作


def test_candidate_success_does_not_hide_failed_take(client, app):
    """take 失敗、候選成功（這頁還沒有角色動作）：候選不算「已有角色動作」，繼續做與再試一次都還會排角色動作。"""
    b, p = book_page(client, app)
    pid = p["id"]
    gen_background(client, app, pid)
    drain(client, app)
    app.state.engine.outcomes.extend([EngineError("ComfyUI 執行失敗：記憶體不足"), None])
    r = client.post(f"/api/pages/{pid}/poses", params={"oneclick": "true"}, json={"action": "揮手", "count": 2})
    assert r.status_code == 202, r.text
    run_jobs(client, app, 2)
    drain(client, app)
    page = get_page(app, pid)
    assert page.pose_generation_id is None and page.attention == "failed"

    plan = client.get(f"/api/books/{b['id']}/produce/plan").json()
    assert plan["poses"] == 1
    r = client.post(f"/api/pages/{pid}/retry")
    assert r.status_code == 200, r.text
    assert r.json()["requeued"] == ["pose"]
    retried = all_gens(app, status=GenStatus.QUEUED)
    assert len(retried) == 1 and rj(app, retried[0].id).get("take") is True


def test_facing_action_consecutive_variations_differ(client, app):
    """有面向語時只有 2 個變化語：一次畫 3 張時第 3 張會跟第 1 張一樣，但相鄰的兩張一定不同。"""
    _b, pages, _ = composed_pages(client, app, n=1)
    pid = pages[0]["id"]
    for _ in range(5):
        before = {g.id for g in all_gens(app)}
        r = client.post(f"/api/pages/{pid}/poses", json={"action": "揮手, facing the viewer", "count": 3})
        assert r.status_code == 202, r.text
        vs = [rj(app, g.id)["variation"] for g in all_gens(app) if g.id not in before]
        assert len(vs) == 3 and all(v in QWEN_POSE_VARIATIONS[2:] for v in vs)
        assert vs[0] != vs[1] and vs[1] != vs[2], vs


def test_inline_take_clears_review_pose(client, app):
    """批次重畫後在 C 自己按「重畫主角」（take，沒有 review）：使用者刻意選的，換上去時清掉 review_pose。"""
    b, pages, _ = composed_pages(client, app, n=1)
    pid = pages[0]["id"]
    redraw(client, b["id"], [pid])
    run_all(client, app)
    assert get_page(app, pid).attention == ATTENTION_REVIEW_POSE

    r = client.post(f"/api/pages/{pid}/poses", params={"oneclick": "true"}, json={"action": "跳舞", "count": 2})
    assert r.status_code == 202, r.text
    run_all(client, app)
    page = get_page(app, pid)
    assert page.pose_generation_id == r.json()["id"]
    assert page.attention is None and page.attention_detail is None
    assert page.status == PageStatus.PASSED
