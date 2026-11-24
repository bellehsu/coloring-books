"""自動清理（spec 2026-11-24 §5.4）：auto_cleanup 頁的 AI 背景好了先不選，排一筆清理它的 generation
（帶著已預留的次數），清好的照新背景的規則選用；清理失敗改用原本那張；背景本身失敗退 2 次。"""

import json

import pytest
from fastapi.testclient import TestClient
from sqlmodel import col, select

from app.config import load_settings
from app.main import create_app
from app.models import Generation, GenKind, GenStatus, PageStatus, PageTask, PageTaskStatus
from app.services import pipeline, usage
from app.services.engine import EngineError
from app.services.gen_request import (
    AUTO_CLEANUP, CLEANUP_RESERVED, USAGE_DAY, USAGE_OWNER, is_auto_cleanup, is_cleanup, wants_take,
)
from app.services.prompts import GENERIC_CLEANUP_PROMPT
from tests.fake_engine import FakeEngine
from tests.test_api_books import run_jobs
from tests.test_api_compose import upload_bg
from tests.test_api_lineart import color_png, get_page
from tests.test_api_pages import new_page
from tests.test_pipeline import book_page, drain
from tests.test_usage import NOW, login, set_cap, used

C = {"oneclick": "true"}
CAP_MSG = "今天的生成次數已用完，明天再來"


@pytest.fixture
def dev_owner():
    return None  # 用真正的邀請連結：額度算在一般 owner 身上


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


def auto(client, pid, on=True, **extra):
    r = client.patch(f"/api/pages/{pid}", json={"auto_cleanup": on, **extra})
    assert r.status_code == 200, r.text


def new_bg(client, pid, *, status=202, **params) -> dict:
    r = client.post(f"/api/pages/{pid}/backgrounds", params=params)
    assert r.status_code == status, r.text
    return r.json()


def new_pose(client, pid) -> int:
    r = client.post(f"/api/pages/{pid}/poses", json={"action": "揮手"})
    assert r.status_code == 202, r.text
    return r.json()["id"]


def bgs(app, pid) -> list[Generation]:
    with app.state.sessions() as s:
        return list(s.exec(select(Generation).where(Generation.page_id == pid, Generation.kind == GenKind.BACKGROUND)
                           .order_by(col(Generation.id))).all())


def row(app, gid) -> Generation:
    with app.state.sessions() as s:
        return s.get(Generation, gid)


def rj(gen: Generation) -> dict:
    return json.loads(gen.request_json)


def cleanups(app, pid) -> list[Generation]:
    return [g for g in bgs(app, pid) if is_cleanup(g)]


def queued_tasks(app, pid) -> list[PageTask]:
    with app.state.sessions() as s:
        return list(s.exec(select(PageTask).where(PageTask.page_id == pid,
                                                  PageTask.status == PageTaskStatus.QUEUED.value)).all())


def setup(client, app, *, cap=60, scene="公園"):
    """邀請 owner（cap 次）＋A5 書＋角色（用掉 1 次）＋一頁。回傳 (owner_id, book, page)。"""
    owner = login(client, app, cap=cap)
    b, p = book_page(client, app, scene=scene)
    return owner, b, p


def overview_state(client, book_id, pid) -> str:
    r = client.get(f"/api/books/{book_id}/overview")
    assert r.status_code == 200, r.text
    return next(p["state"] for p in r.json()["pages"] if p["id"] == pid)


# ---- 自動清理：觸發、帶著次數、不連鎖 ----------------------------------------------------


def test_auto_background_queues_cleanup_with_reserved_count_then_lands(client, app):
    owner, b, p = setup(client, app)
    pid = p["id"]
    auto(client, pid)
    base = used(app, owner)

    out = new_bg(client, pid)
    assert used(app, owner) == base + 2  # 背景＋它的自動清理
    src_row = row(app, out["id"])
    assert rj(src_row)[CLEANUP_RESERVED] is True

    run_jobs(client, app)  # 背景好了：不選用，改排清理
    pg = get_page(app, pid)
    assert pg.background_generation_id is None and pg.auto_background_generation_id is None
    assert queued_tasks(app, pid) == []  # 不排頁面流程（等清好的）
    (cl,) = cleanups(app, pid)
    assert cl.status == GenStatus.QUEUED and cl.source_generation_id == out["id"]
    assert is_auto_cleanup(cl) and not wants_take(cl)
    assert cl.prompt == GENERIC_CLEANUP_PROMPT and cl.user_text == src_row.user_text == "公園"
    assert [cl.width, cl.height] == [src_row.width, src_row.height]
    src = rj(row(app, out["id"]))
    assert src[CLEANUP_RESERVED] is False  # 那 1 次帶走了
    assert rj(cl)[USAGE_DAY] == src[USAGE_DAY] and rj(cl)[USAGE_OWNER] == src[USAGE_OWNER] == owner
    assert used(app, owner) == base + 2  # 沒有再預留
    assert overview_state(client, b["id"], pid) == "working"  # 清理排隊中：這頁算製作中

    run_jobs(client, app)  # 清好了：照新背景的規則選用、排流程
    pg = get_page(app, pid)
    assert pg.background_generation_id == cl.id == pg.auto_background_generation_id
    assert len(queued_tasks(app, pid)) == 1
    assert len(cleanups(app, pid)) == 1  # 清理好了不再觸發清理
    assert app.state.engine.requests[-1].source_image is not None
    assert used(app, owner) == base + 2

    new_pose(client, pid)
    run_jobs(client, app)
    drain(client, app)
    pg = get_page(app, pid)
    assert pg.status == PageStatus.PASSED and pg.background_generation_id == cl.id
    assert len(cleanups(app, pid)) == 1 and used(app, owner) == base + 3


def test_auto_cleanup_uses_page_prompt_and_copies_oneclick_take(client, app):
    owner, b, p = setup(client, app)
    pid = p["id"]
    first = new_bg(client, pid)["id"]  # 還沒開自動清理：直接選用
    run_jobs(client, app)
    new_pose(client, pid)
    run_jobs(client, app)
    drain(client, app)
    assert get_page(app, pid).background_generation_id == first and cleanups(app, pid) == []
    mine = "Delete the clover border."
    auto(client, pid, cleanup_prompt=mine)
    base = used(app, owner)

    out = new_bg(client, pid, **C)  # 一鍵的「AI 再畫一張」
    assert used(app, owner) == base + 2
    run_jobs(client, app)
    (cl,) = cleanups(app, pid)
    assert cl.prompt == mine and wants_take(cl) and cl.source_generation_id == out["id"]
    assert get_page(app, pid).background_generation_id == first  # 來源沒換上去

    run_jobs(client, app)  # take：清好的一定換上去
    pg = get_page(app, pid)
    assert pg.background_generation_id == cl.id == pg.auto_background_generation_id
    drain(client, app)
    assert get_page(app, pid).status == PageStatus.PASSED
    assert len(cleanups(app, pid)) == 1 and used(app, owner) == base + 2


def test_pipeline_does_not_pick_the_uncleaned_background(client, app):
    """背景好了、清理還在排隊時角色動作先好了：流程不拿沒清過的那張，等清好的。"""
    owner, b, p = setup(client, app)
    pid = p["id"]
    auto(client, pid)
    src = new_bg(client, pid)["id"]
    new_pose(client, pid)
    run_jobs(client, app)  # 背景 → 排清理（排在角色動作後面）
    run_jobs(client, app)  # 角色動作 → 排流程
    drain(client, app)
    pg = get_page(app, pid)
    assert pg.background_generation_id is None and pg.status != PageStatus.PASSED
    assert overview_state(client, b["id"], pid) == "working"
    run_jobs(client, app)  # 清理
    drain(client, app)
    pg = get_page(app, pid)
    (cl,) = cleanups(app, pid)
    assert cl.source_generation_id == src
    assert pg.background_generation_id == cl.id and pg.status == PageStatus.PASSED


# ---- 失敗與退回 -------------------------------------------------------------------------


def test_failed_auto_cleanup_selects_source_and_runs_pipeline(client, app):
    owner, b, p = setup(client, app)
    pid = p["id"]
    auto(client, pid)
    base = used(app, owner)
    src = new_bg(client, pid)["id"]
    new_pose(client, pid)
    run_jobs(client, app, 2)  # 背景（排清理）、角色動作
    app.state.engine.outcomes.append(EngineError("ComfyUI 執行失敗：記憶體不足"))
    run_jobs(client, app)  # 清理失敗
    (cl,) = cleanups(app, pid)
    assert cl.status == GenStatus.FAILED and cl.error
    pg = get_page(app, pid)
    assert pg.background_generation_id == src == pg.auto_background_generation_id
    assert pg.attention is None
    assert queued_tasks(app, pid)
    assert used(app, owner) == base + 2  # 背景 2 次 + 角色動作 1 次，清理失敗退 1 次
    drain(client, app)
    assert get_page(app, pid).status == PageStatus.PASSED
    assert len(cleanups(app, pid)) == 1  # 失敗不重排清理


def test_failed_auto_page_background_refunds_both_counts(client, app):
    owner, b, p = setup(client, app)
    pid = p["id"]
    auto(client, pid)
    base = used(app, owner)
    app.state.engine.outcomes.append(EngineError("ComfyUI 執行失敗"))
    new_bg(client, pid)
    assert used(app, owner) == base + 2
    run_jobs(client, app)
    assert used(app, owner) == base  # 背景＋預留的清理都退回
    assert cleanups(app, pid) == [] and get_page(app, pid).attention == "failed"


def test_unreserved_background_reserves_cleanup_at_trigger(client, app):
    """排隊後才打開自動清理（背景沒預留）：觸發時才預留 1 次。"""
    owner, b, p = setup(client, app)
    pid = p["id"]
    src = new_bg(client, pid)["id"]
    base = used(app, owner)
    assert CLEANUP_RESERVED not in rj(row(app, src))
    auto(client, pid)
    run_jobs(client, app)
    (cl,) = cleanups(app, pid)
    assert cl.source_generation_id == src and is_auto_cleanup(cl)
    assert used(app, owner) == base + 1
    assert rj(cl)[USAGE_OWNER] == owner


def test_unreserved_background_with_quota_used_up_is_selected_without_cleanup(client, app):
    owner, b, p = setup(client, app, cap=3)  # 角色 1 次
    pid = p["id"]
    src = new_bg(client, pid)["id"]  # 1 次（還沒開自動清理）
    assert used(app, owner) == 2
    auto(client, pid)
    set_cap(app, owner, 2)  # 次數用完了
    run_jobs(client, app)
    assert cleanups(app, pid) == []
    pg = get_page(app, pid)
    assert pg.background_generation_id == src == pg.auto_background_generation_id
    assert len(queued_tasks(app, pid)) == 1 and used(app, owner) == 2


def test_reserved_cleanup_refunded_when_auto_cleanup_turned_off(client, app):
    """預留了清理，背景好之前改了場景文字（自動清理關掉）：不清、照常選用，那 1 次退回。"""
    owner, b, p = setup(client, app)
    pid = p["id"]
    auto(client, pid)
    base = used(app, owner)
    src = new_bg(client, pid)["id"]
    auto(client, pid, False)
    run_jobs(client, app)
    assert cleanups(app, pid) == []
    assert get_page(app, pid).background_generation_id == src
    assert used(app, owner) == base + 1
    assert rj(row(app, src))[CLEANUP_RESERVED] is False


# ---- 次數：換一張／AI 再畫一張／批次 --------------------------------------------------------


@pytest.mark.parametrize("params", [{}, C])
def test_new_background_on_auto_page_needs_two_counts(client, app, params):
    owner, b, p = setup(client, app, cap=3)  # 角色 1 次：剩 2
    pid = p["id"]
    auto(client, pid)
    set_cap(app, owner, 2)  # 只剩 1 次
    r = client.post(f"/api/pages/{pid}/backgrounds", params=params)
    assert r.status_code == 429 and r.json()["detail"] == "今天只剩 1 次，這次需要 2 次"
    assert bgs(app, pid) == [] and used(app, owner) == 1


def test_materials_batch_counts_auto_backgrounds_twice(client, app):
    owner, b, p = setup(client, app)
    other = new_page(client, b["id"], scene_prompt="海邊", action_prompt="跳")
    auto(client, p["id"])
    base = used(app, owner)
    r = client.post(f"/api/books/{b['id']}/materials/batch")
    assert r.status_code == 202, r.text
    assert len(r.json()["queued"]) == 4
    assert used(app, owner) == base + 2 + 1 + 1 + 1
    (auto_bg,) = bgs(app, p["id"])
    (plain_bg,) = bgs(app, other["id"])
    assert rj(auto_bg)[CLEANUP_RESERVED] is True and CLEANUP_RESERVED not in rj(plain_bg)


def test_retry_failed_auto_background_needs_two_counts(client, app):
    owner, b, p = setup(client, app)
    pid = p["id"]
    auto(client, pid)
    app.state.engine.outcomes.append(EngineError("ComfyUI 執行失敗"))
    new_bg(client, pid)
    run_jobs(client, app)
    base = used(app, owner)
    r = client.post(f"/api/pages/{pid}/retry")
    assert r.status_code == 200, r.text
    assert used(app, owner) == base + 2
    assert rj(bgs(app, pid)[-1])[CLEANUP_RESERVED] is True


# ---- 不受影響的情況 ---------------------------------------------------------------------


def test_non_auto_page_is_unchanged(client, app):
    owner, b, p = setup(client, app)
    pid = p["id"]
    base = used(app, owner)
    out = new_bg(client, pid)
    assert used(app, owner) == base + 1
    d = rj(row(app, out["id"]))
    assert CLEANUP_RESERVED not in d and AUTO_CLEANUP not in d
    run_jobs(client, app)
    pg = get_page(app, pid)
    assert pg.background_generation_id == out["id"] == pg.auto_background_generation_id
    assert cleanups(app, pid) == [] and len(queued_tasks(app, pid)) == 1
    assert used(app, owner) == base + 1


def test_uploaded_background_on_auto_page_is_not_cleaned(client, app):
    owner, b, p = setup(client, app)
    pid = p["id"]
    auto(client, pid)
    base = used(app, owner)
    r = upload_bg(client, pid, color_png())
    assert r.status_code == 201, r.text
    up = get_page(app, pid).background_generation_id
    with app.state.sessions() as s:  # 就算有人把上傳的當成新背景通知，也不清
        pipeline.on_generation_succeeded(s, up)
    assert cleanups(app, pid) == [] and used(app, owner) == base
    assert get_page(app, pid).background_generation_id == up


def test_button_cleanup_on_auto_page_does_not_chain(client, app):
    """auto 頁上按「清掉框外雜物」：清好的照按鈕規則換上，不再觸發清理。"""
    owner, b, p = setup(client, app)
    pid = p["id"]
    new_bg(client, pid)
    run_jobs(client, app)
    new_pose(client, pid)
    run_jobs(client, app)
    drain(client, app)
    src = get_page(app, pid).background_generation_id
    auto(client, pid)
    r = client.put(f"/api/pages/{pid}/placement", json={
        "cx": 0.5, "cy": 0.5, "height": 0.8, "flip": False, "frame": {"x": 0.5, "y": 0.5}, "autofit": False})
    assert r.status_code == 200, r.text
    base = used(app, owner)
    r = client.post(f"/api/pages/{pid}/backgrounds/cleanup")
    assert r.status_code == 202, r.text
    assert used(app, owner) == base + 1
    run_jobs(client, app)
    (cl,) = cleanups(app, pid)
    assert not is_auto_cleanup(cl) and cl.source_generation_id == src
    assert get_page(app, pid).background_generation_id == cl.id
    assert used(app, owner) == base + 1


# ---- 自動清理被取消／捨棄、排清理時出錯：頁面不卡住 ------------------------------------------------


def _auto_page_with_queued_cleanup(client, app, **params):
    """auto 頁：背景好了（排了清理）、角色動作好了、角色動作的流程跑完（等清好的背景）。回傳 (owner, book, page, 來源, 清理)。"""
    owner, b, p = setup(client, app)
    pid = p["id"]
    auto(client, pid)
    src = new_bg(client, pid, **params)["id"]
    new_pose(client, pid)
    run_jobs(client, app, 2)
    drain(client, app)
    (cl,) = cleanups(app, pid)
    assert cl.status == GenStatus.QUEUED and get_page(app, pid).background_generation_id is None
    return owner, b, p, src, cl


def test_cancelled_auto_cleanup_selects_source_and_runs_pipeline(client, app):
    owner, b, p, src, cl = _auto_page_with_queued_cleanup(client, app)
    pid = p["id"]
    base = used(app, owner)
    r = client.post(f"/api/generations/{cl.id}/cancel")
    assert r.status_code == 200, r.text
    assert used(app, owner) == base - 1  # 清理的那 1 次退回
    pg = get_page(app, pid)
    assert pg.background_generation_id == src == pg.auto_background_generation_id
    assert pg.attention is None and len(queued_tasks(app, pid)) == 1
    drain(client, app)
    assert get_page(app, pid).status == PageStatus.PASSED
    assert overview_state(client, b["id"], pid) == "ready"


def test_discarded_queued_auto_cleanup_selects_source_and_runs_pipeline(client, app):
    owner, b, p, src, cl = _auto_page_with_queued_cleanup(client, app)
    pid = p["id"]
    base = used(app, owner)
    r = client.patch(f"/api/generations/{cl.id}", json={"discarded": True})
    assert r.status_code == 200, r.text
    assert row(app, cl.id).status == GenStatus.CANCELLED and used(app, owner) == base - 1
    assert get_page(app, pid).background_generation_id == src
    drain(client, app)
    assert get_page(app, pid).status == PageStatus.PASSED


def test_auto_cleanup_discarded_while_running_selects_source(client, app):
    """執行中被捨棄（狀態不變、好了也不能用）：好了時改用來源背景。"""
    owner, b, p, src, cl = _auto_page_with_queued_cleanup(client, app)
    pid = p["id"]
    with app.state.sessions() as s:
        g = s.get(Generation, cl.id)
        g.discarded = True
        s.add(g)
        s.commit()
    run_jobs(client, app)
    assert row(app, cl.id).status == GenStatus.SUCCEEDED
    assert get_page(app, pid).background_generation_id == src
    drain(client, app)
    assert get_page(app, pid).status == PageStatus.PASSED


def test_cancelled_oneclick_auto_cleanup_takes_source_unless_background_changed(client, app):
    owner, b, p = setup(client, app)
    pid = p["id"]
    first = new_bg(client, pid)["id"]
    run_jobs(client, app)
    new_pose(client, pid)
    run_jobs(client, app)
    drain(client, app)
    auto(client, pid)

    src = new_bg(client, pid, **C)["id"]  # 一鍵：取消清理後照 take 換上來源
    run_jobs(client, app)
    (cl,) = cleanups(app, pid)
    assert client.post(f"/api/generations/{cl.id}/cancel").status_code == 200
    pg = get_page(app, pid)
    assert pg.background_generation_id == src == pg.auto_background_generation_id
    drain(client, app)

    src2 = new_bg(client, pid, **C)["id"]  # 清理排隊時使用者改選了 first：取消後不硬換
    run_jobs(client, app)
    cl2 = cleanups(app, pid)[-1]
    assert cl2.source_generation_id == src2
    r = client.post(f"/api/pages/{pid}/background", json={"generation_id": first})
    assert r.status_code == 200, r.text
    assert client.post(f"/api/generations/{cl2.id}/cancel").status_code == 200
    assert get_page(app, pid).background_generation_id == first
    assert len(queued_tasks(app, pid)) == 1


def test_failure_while_queuing_auto_cleanup_refunds_and_selects_source(client, app, monkeypatch):
    owner, b, p = setup(client, app)
    pid = p["id"]
    auto(client, pid)
    base = used(app, owner)
    src = new_bg(client, pid)["id"]

    def boom(*a, **k):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(pipeline, "add_queued", boom)
    run_jobs(client, app)  # worker 的保底：退回預留的清理次數、選用來源、排流程
    assert cleanups(app, pid) == []
    assert used(app, owner) == base + 1
    assert rj(row(app, src))[CLEANUP_RESERVED] is False
    pg = get_page(app, pid)
    assert pg.background_generation_id == src
    assert len(queued_tasks(app, pid)) == 1


def test_cancelled_auto_page_background_refunds_both_counts(client, app):
    owner, b, p = setup(client, app)
    pid = p["id"]
    auto(client, pid)
    base = used(app, owner)
    gid = new_bg(client, pid)["id"]
    assert used(app, owner) == base + 2
    assert client.post(f"/api/generations/{gid}/cancel").status_code == 200
    assert used(app, owner) == base and cleanups(app, pid) == []
