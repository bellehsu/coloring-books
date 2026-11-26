"""不放主角（spec 2026-11-26-no-character-pages §5.3）：已做好的頁切換主角 POST /api/pages/{id}/character。

要重畫背景的切換不立刻改 include_character：意圖記在那筆背景（request_json 的 set_character），新背景換上時才一起改；
失敗或取消時頁面維持原狀。保留背景的切換（0 次或只排角色動作）立即改。一鍵處理頁與進階編輯都一定換上（take）。"""

import json

import pytest
from fastapi.testclient import TestClient
from sqlmodel import col, select

from app.config import load_settings
from app.main import create_app
from app.models import Book, Generation, GenKind, GenStatus, Owner, PageHistory, PageStatus, Usage
from app.services import auth, usage
from app.services.engine import EngineError
from app.services.gen_request import SET_CHARACTER, set_character_of, wants_take
from app.services.jobs import NO_CHARACTER_SKIP
from app.services.prompts import QWEN_BACKGROUND_CENTER_VARIATIONS, QWEN_BACKGROUND_VARIATIONS
from tests.fake_engine import FakeEngine
from tests.test_api_books import run_jobs
from tests.test_api_compose import composites, gen_background, gen_row, png_bytes
from tests.test_api_lineart import get_page, legacy_page, page_out, set_page
from tests.test_pipeline import book_page, drain, queued

C = {"oneclick": "true"}
CAP_MSG = "今天的生成次數已用完，明天再來"
FRAME_NEEDS_CHARACTER = "相框頁要放主角"
UPLOAD_NOT_REDRAWN = "上傳的背景不會重畫"
TOGGLE_PENDING = "這頁正在切換主角，請等一下"
NC_SCENE = "海邊，畫面右邊有一座小小的燈塔"
BIG = "畫得很大"
PORTRAIT = {"cx": 0.5, "cy": 0.5, "height": 0.8, "flip": False, "frame": {"x": 0.5, "y": 0.5}, "autofit": False}


@pytest.fixture
def dev_owner():
    return None  # 用真正的邀請連結：額度算在一般 owner 身上


@pytest.fixture
def app(settings_file):
    return create_app(load_settings(settings_file), engine=FakeEngine(), start_worker=False)


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        login(c, app)
        yield c


def login(client, app, name="甲", *, cap=60) -> int:
    with app.state.sessions() as s:
        owner, token = auth.create_owner(s, name, cap=cap)
    client.cookies.clear()
    assert client.get(f"/i/{token}", follow_redirects=False).status_code == 303
    return owner.id


def owner_of(app, book_id) -> int:
    with app.state.sessions() as s:
        return s.get(Book, book_id).owner_id


def used(app, owner_id) -> int:
    with app.state.sessions() as s:
        row = s.get(Usage, (owner_id, usage.taipei_day(usage.clock())))
        return 0 if row is None else row.generations


def set_cap(app, owner_id, cap):
    with app.state.sessions() as s:
        o = s.get(Owner, owner_id)
        o.daily_generation_cap = cap
        s.add(o)
        s.commit()


def toggle(client, pid, include, *, status=None, redraw_background=None, **params):
    body = {"include": include}
    if redraw_background is not None:
        body["redraw_background"] = redraw_background
    r = client.post(f"/api/pages/{pid}/character", json=body, params=params)
    if status is not None:
        assert r.status_code == status, r.text
    return r


def page_gens(app, pid, kind=None) -> list[Generation]:
    with app.state.sessions() as s:
        q = select(Generation).where(Generation.page_id == pid).order_by(col(Generation.id))
        if kind is not None:
            q = q.where(Generation.kind == kind)
        return list(s.exec(q).all())


def active(app, pid) -> list[Generation]:
    return [g for g in page_gens(app, pid) if g.status in (GenStatus.QUEUED, GenStatus.RUNNING)]


def history_count(app, pid) -> int:
    with app.state.sessions() as s:
        return len(s.exec(select(PageHistory).where(PageHistory.page_id == pid)).all())


def set_status(app, gen_id, status):
    with app.state.sessions() as s:
        g = s.get(Generation, gen_id)
        g.status = status
        s.add(g)
        s.commit()


def selected_params(app, pid) -> dict:
    return json.loads(gen_row(app, get_page(app, pid).selected_generation_id).params_json)


def produced(client, app, scene=NC_SCENE):
    """背景＋角色動作產生完、流程跑完（PASSED）。回傳 (book, page id, 背景 id, 角色動作 id)。"""
    from tests.test_api_compose import gen_pose

    b, p = book_page(client, app, scene=scene)
    pid = p["id"]
    bg = gen_background(client, app, pid)
    pose = gen_pose(client, app, pid)
    drain(client, app)
    assert get_page(app, pid).status == PageStatus.PASSED
    return b, pid, bg, pose


def off_page(client, app):
    """做好的頁切成不放主角、新背景換上、流程跑完。回傳 (book, page id, 舊背景, 角色動作, 新背景)。"""
    b, pid, bg, pose = produced(client, app)
    out = toggle(client, pid, False, status=202).json()
    assert out["character_pending"] is True
    run_jobs(client, app)
    drain(client, app)
    pg = get_page(app, pid)
    assert pg.include_character is False and pg.status == PageStatus.PASSED
    return b, pid, bg, pose, pg.background_generation_id


def bg_only_page(client, app):
    """不放主角、只有背景（沒有角色動作）、流程跑完。"""
    b, p = book_page(client, app, scene=NC_SCENE)
    pid = p["id"]
    bg = gen_background(client, app, pid)
    set_page(app, pid, include_character=False)
    drain(client, app)
    pg = get_page(app, pid)
    assert pg.status == PageStatus.PASSED and pg.pose_generation_id is None
    return b, pid, bg


def upload_selected(client, app, pid) -> int:
    r = client.post(f"/api/pages/{pid}/backgrounds/upload", params=C,
                    files={"file": ("mine.png", png_bytes(), "image/png")})
    assert r.status_code == 201, r.text
    drain(client, app)
    return get_page(app, pid).background_generation_id


# ---- 關掉主角 -------------------------------------------------------------------------


def test_toggle_off_redraws_then_flips(client, app):
    b, pid, bg, pose = produced(client, app)
    owner = owner_of(app, b["id"])
    before = used(app, owner)
    n_hist = history_count(app, pid)

    out = toggle(client, pid, False, status=202).json()
    assert out["include_character"] is True and out["character_pending"] is True  # 等換上才改
    [job] = active(app, pid)
    assert job.kind == GenKind.BACKGROUND and wants_take(job) and set_character_of(job) is False
    assert BIG in job.prompt and json.loads(job.request_json)["variation"] in QWEN_BACKGROUND_CENTER_VARIATIONS
    assert job.user_text == NC_SCENE
    assert used(app, owner) == before + 1
    assert get_page(app, pid).include_character is True and history_count(app, pid) == n_hist

    run_jobs(client, app)
    pg = get_page(app, pid)
    assert pg.include_character is False
    assert pg.background_generation_id == job.id == pg.auto_background_generation_id
    assert history_count(app, pid) == n_hist + 1  # 換上前存快照
    assert page_out(client, b["id"], pid)["character_pending"] is False
    drain(client, app)
    pg = get_page(app, pid)
    assert pg.status == PageStatus.PASSED
    params = selected_params(app, pid)
    assert params["background_generation_id"] == job.id and params["pose_generation_id"] is None  # 合成圖＝新背景


def test_toggle_off_failure_keeps_page(client, app):
    b, pid, bg, pose = produced(client, app)
    owner = owner_of(app, b["id"])
    before = used(app, owner)
    selected = get_page(app, pid).selected_generation_id
    app.state.engine.outcomes.append(EngineError("ComfyUI 執行失敗：記憶體不足"))
    out = toggle(client, pid, False, status=202).json()
    run_jobs(client, app)
    drain(client, app)
    [job] = page_gens(app, pid, GenKind.BACKGROUND)[1:]
    assert job.status == GenStatus.FAILED
    pg = get_page(app, pid)
    assert pg.include_character is True and pg.background_generation_id == bg
    assert pg.selected_generation_id == selected
    assert pg.attention == "failed"  # 照現有的背景失敗規則
    assert used(app, owner) == before  # 失敗退回
    assert page_out(client, b["id"], pid)["character_pending"] is False
    assert out["include_character"] is True


def test_retry_after_toggle_failure_redraws_the_toggle(client, app):
    """切換的背景失敗後按「再試一次」：照那次切換再畫（帶 set_character），換上時才改欄位。"""
    b, pid, bg, pose = produced(client, app)
    app.state.engine.outcomes.append(EngineError("ComfyUI 執行失敗：記憶體不足"))
    toggle(client, pid, False, status=202)
    run_jobs(client, app)
    assert get_page(app, pid).attention == "failed"
    r = client.post(f"/api/pages/{pid}/retry")
    assert r.status_code == 200, r.text
    assert r.json()["requeued"] == ["background"] and r.json()["character_pending"] is True
    [job] = active(app, pid)
    assert set_character_of(job) is False and wants_take(job) and BIG in job.prompt
    run_jobs(client, app)
    drain(client, app)
    pg = get_page(app, pid)
    assert pg.include_character is False and pg.background_generation_id == job.id
    assert pg.status == PageStatus.PASSED and pg.attention is None


def test_retry_pipeline_on_no_character_page(client, app):
    """不放主角的頁流程失敗：「再試一次」只要有背景就重跑流程（不要求角色動作）。"""
    b, pid, bg = bg_only_page(client, app)
    set_page(app, pid, attention="failed", attention_detail="x")
    r = client.post(f"/api/pages/{pid}/retry")
    assert r.status_code == 200, r.text
    assert r.json()["pipeline"] is True and queued(app, pid)


def test_toggle_off_cancel_keeps_page(client, app):
    b, pid, bg, pose = produced(client, app)
    owner = owner_of(app, b["id"])
    before = used(app, owner)
    toggle(client, pid, False, status=202)
    [job] = active(app, pid)
    r = client.post(f"/api/generations/{job.id}/cancel")
    assert r.status_code == 200, r.text
    assert not client.portal.call(app.state.worker.run_once)  # 沒有東西可跑
    drain(client, app)
    pg = get_page(app, pid)
    assert pg.include_character is True and pg.background_generation_id == bg
    assert pg.status == PageStatus.PASSED
    assert used(app, owner) == before
    assert page_out(client, b["id"], pid)["character_pending"] is False


def test_upload_background_toggle_off_is_free(client, app):
    b, pid, bg, pose = produced(client, app)
    upload = upload_selected(client, app, pid)
    owner = owner_of(app, b["id"])
    before = used(app, owner)
    n_gens = len(page_gens(app, pid))
    n_hist = history_count(app, pid)

    out = toggle(client, pid, False, status=200).json()
    assert out["include_character"] is False and out["character_pending"] is False
    assert len(page_gens(app, pid)) == n_gens and used(app, owner) == before  # 0 次
    assert history_count(app, pid) == n_hist + 1
    assert queued(app, pid)  # 排了頁面流程
    drain(client, app)
    pg = get_page(app, pid)
    assert pg.status == PageStatus.PASSED and pg.background_generation_id == upload
    params = selected_params(app, pid)
    assert params["background_generation_id"] == upload and params["pose_generation_id"] is None


def test_toggle_off_without_background_or_redraw_is_immediate(client, app):
    b, p = book_page(client, app, scene=NC_SCENE)
    pid = p["id"]
    assert toggle(client, pid, False, status=200).json()["include_character"] is False
    assert page_gens(app, pid) == []
    # 有 AI 背景但 redraw_background=false：立即改，不畫
    b, pid, bg, pose = produced(client, app)
    n = len(page_gens(app, pid))
    out = toggle(client, pid, False, redraw_background=False, status=200).json()
    assert out["include_character"] is False and len(page_gens(app, pid)) == n
    drain(client, app)
    params = selected_params(app, pid)
    assert params["background_generation_id"] == bg and params["pose_generation_id"] is None


# ---- 加回主角 -------------------------------------------------------------------------


def test_toggle_on_keep_background_uses_existing_pose(client, app):
    b, pid, old_bg, pose, new_bg = off_page(client, app)
    owner = owner_of(app, b["id"])
    before = used(app, owner)
    n_gens = len(page_gens(app, pid))
    n_hist = history_count(app, pid)

    out = toggle(client, pid, True, redraw_background=False, status=200).json()
    assert out["include_character"] is True and out["character_pending"] is False
    assert len(page_gens(app, pid)) == n_gens and used(app, owner) == before  # 0 次
    assert history_count(app, pid) == n_hist + 1
    assert queued(app, pid)
    drain(client, app)
    pg = get_page(app, pid)
    assert pg.status == PageStatus.PASSED and pg.background_generation_id == new_bg
    params = selected_params(app, pid)
    assert params["background_generation_id"] == new_bg and params["pose_generation_id"] == pose


def test_toggle_on_keep_background_queues_pose(client, app):
    b, pid, bg = bg_only_page(client, app)
    owner = owner_of(app, b["id"])
    before = used(app, owner)
    out = toggle(client, pid, True, redraw_background=False, status=202).json()
    assert out["include_character"] is True and out["character_pending"] is False  # 立即改
    [job] = active(app, pid)
    assert job.kind == GenKind.POSE and wants_take(job) and set_character_of(job) is None
    assert used(app, owner) == before + 1
    run_jobs(client, app)
    drain(client, app)
    pg = get_page(app, pid)
    assert pg.pose_generation_id == job.id and pg.background_generation_id == bg
    assert pg.status == PageStatus.PASSED
    assert selected_params(app, pid)["pose_generation_id"] == job.id


def test_toggle_on_redraw_queues_bg_and_pose(client, app):
    b, pid, bg = bg_only_page(client, app)
    owner = owner_of(app, b["id"])
    before = used(app, owner)
    out = toggle(client, pid, True, status=202).json()  # redraw_background 預設 true
    assert out["include_character"] is False and out["character_pending"] is True
    new_bg, new_pose = active(app, pid)
    assert new_bg.kind == GenKind.BACKGROUND and wants_take(new_bg) and set_character_of(new_bg) is True
    assert BIG not in new_bg.prompt
    assert json.loads(new_bg.request_json)["variation"] in QWEN_BACKGROUND_VARIATIONS
    assert new_pose.kind == GenKind.POSE and wants_take(new_pose)
    assert used(app, owner) == before + 2
    run_jobs(client, app, 2)
    drain(client, app)
    pg = get_page(app, pid)
    assert pg.include_character is True
    assert pg.background_generation_id == new_bg.id and pg.pose_generation_id == new_pose.id
    assert pg.status == PageStatus.PASSED
    params = selected_params(app, pid)
    assert params["background_generation_id"] == new_bg.id and params["pose_generation_id"] == new_pose.id


def test_toggle_on_redraw_with_existing_pose_is_one(client, app):
    b, pid, old_bg, pose, new_bg = off_page(client, app)
    owner = owner_of(app, b["id"])
    before = used(app, owner)
    toggle(client, pid, True, status=202)
    [job] = active(app, pid)
    assert job.kind == GenKind.BACKGROUND and set_character_of(job) is True
    assert used(app, owner) == before + 1
    run_jobs(client, app)
    drain(client, app)
    pg = get_page(app, pid)
    assert pg.include_character is True and pg.background_generation_id == job.id
    assert selected_params(app, pid)["pose_generation_id"] == pose


def test_upload_redraw_409(client, app):
    b, pid, bg, pose = produced(client, app)
    upload_selected(client, app, pid)
    toggle(client, pid, False, status=200)
    owner = owner_of(app, b["id"])
    before = used(app, owner)
    n = len(page_gens(app, pid))
    assert toggle(client, pid, True, status=409).json() == {"detail": UPLOAD_NOT_REDRAWN}
    assert len(page_gens(app, pid)) == n and used(app, owner) == before
    assert get_page(app, pid).include_character is False
    assert toggle(client, pid, True, redraw_background=False, status=200).json()["include_character"] is True


def test_toggle_same_value_is_noop(client, app):
    b, pid, bg, pose = produced(client, app)
    n_gens, n_hist = len(page_gens(app, pid)), history_count(app, pid)
    out = toggle(client, pid, True, status=200).json()
    assert out["include_character"] is True
    assert len(page_gens(app, pid)) == n_gens and history_count(app, pid) == n_hist
    assert not queued(app, pid)


# ---- 不能切換 -------------------------------------------------------------------------


def test_frame_page_409(client, app):
    b, pid, bg, pose = produced(client, app)
    r = client.put(f"/api/pages/{pid}/placement", json=PORTRAIT)
    assert r.status_code == 200, r.text
    owner = owner_of(app, b["id"])
    before = used(app, owner)
    n = len(page_gens(app, pid))
    assert toggle(client, pid, False, status=409).json() == {"detail": FRAME_NEEDS_CHARACTER}
    assert toggle(client, pid, False, redraw_background=False, status=409).json() == {"detail": FRAME_NEEDS_CHARACTER}
    assert len(page_gens(app, pid)) == n and used(app, owner) == before
    assert get_page(app, pid).include_character is True


def test_toggle_quota_429(client, app):
    b, pid, bg, pose = produced(client, app)
    b2, pid2, bg2 = bg_only_page(client, app)
    owner = owner_of(app, b["id"])
    assert owner_of(app, b2["id"]) == owner
    set_cap(app, owner, used(app, owner))
    n, n_hist = len(page_gens(app, pid)), history_count(app, pid)
    assert toggle(client, pid, False, status=429).json() == {"detail": CAP_MSG}
    assert len(page_gens(app, pid)) == n and history_count(app, pid) == n_hist
    assert get_page(app, pid).include_character is True
    # 要 2 次只剩 1 次：一筆都不排、欄位不動
    set_cap(app, owner, used(app, owner) + 1)
    n2 = len(page_gens(app, pid2))
    assert toggle(client, pid2, True, status=429).json() == {"detail": "今天只剩 1 次，這次需要 2 次"}
    assert len(page_gens(app, pid2)) == n2 and get_page(app, pid2).include_character is False
    # 保留背景但要排角色動作：次數不夠也是 429，欄位不改、不存快照
    set_cap(app, owner, used(app, owner))
    n_hist2 = history_count(app, pid2)
    assert toggle(client, pid2, True, redraw_background=False, status=429).json() == {"detail": CAP_MSG}
    assert get_page(app, pid2).include_character is False and history_count(app, pid2) == n_hist2


def test_toggle_legacy_or_empty_scene_409(client, app):
    from app.api.compose import LEGACY_NO_TOGGLE, NO_SCENE_TO_REDRAW

    b, p, _ = legacy_page(client, app, scene_prompt="公園", action_prompt="揮手")
    pid = p["id"]
    n = len(page_gens(app, pid))
    assert toggle(client, pid, False, status=409).json() == {"detail": LEGACY_NO_TOGGLE}
    assert len(page_gens(app, pid)) == n and get_page(app, pid).include_character is True

    b, pid, bg, pose = produced(client, app)
    set_page(app, pid, scene_prompt="  ")
    owner = owner_of(app, b["id"])
    before = used(app, owner)
    n = len(page_gens(app, pid))
    assert toggle(client, pid, False, status=409).json() == {"detail": NO_SCENE_TO_REDRAW}
    assert len(page_gens(app, pid)) == n and used(app, owner) == before
    assert get_page(app, pid).include_character is True
    # 不用重畫（保留背景）：照樣可以切
    assert toggle(client, pid, False, redraw_background=False, status=200).json()["include_character"] is False


def test_toggle_other_owners_page_is_404(client, app):
    b, pid, bg, pose = produced(client, app)
    login(client, app, "乙")
    assert toggle(client, pid, False, status=404).json() == {"detail": "找不到這一頁"}


def test_toggle_body_validation(client, app):
    b, pid, bg, pose = produced(client, app)
    assert client.post(f"/api/pages/{pid}/character", json={}).status_code == 422
    assert client.post(f"/api/pages/{pid}/character", json={"include": "no"}).status_code == 422
    assert client.post(f"/api/pages/{pid}/character",
                       json={"include": False, "redraw_background": None}).status_code == 422


# ---- 連續切換、切換中 -----------------------------------------------------------------


def test_toggle_while_background_running(client, app):
    b, pid, bg, pose = produced(client, app)
    owner = owner_of(app, b["id"])
    before = used(app, owner)

    # 排隊中再切換：取消那筆（退回次數）再照新請求處理（加回主角＝跟目前一樣：只取消）
    toggle(client, pid, False, status=202)
    [first] = active(app, pid)
    out = toggle(client, pid, True, redraw_background=False, status=200).json()
    assert out["include_character"] is True and out["character_pending"] is False
    assert gen_row(app, first.id).status == GenStatus.CANCELLED
    assert used(app, owner) == before and active(app, pid) == []

    # 排隊中再送一樣的關掉：取消舊的、排新的
    toggle(client, pid, False, status=202)
    [second] = active(app, pid)
    toggle(client, pid, False, status=202)
    [third] = active(app, pid)
    assert third.id != second.id and gen_row(app, second.id).status == GenStatus.CANCELLED
    assert used(app, owner) == before + 1

    # 排隊中改成「關掉、保留背景」：取消、立即改
    out = toggle(client, pid, False, redraw_background=False, status=200).json()
    assert out["include_character"] is False and gen_row(app, third.id).status == GenStatus.CANCELLED
    assert used(app, owner) == before and active(app, pid) == []

    # 執行中：409，什麼都不排
    toggle(client, pid, True, status=202)
    [fourth] = active(app, pid)
    set_status(app, fourth.id, GenStatus.RUNNING)
    n = len(page_gens(app, pid))
    assert toggle(client, pid, False, redraw_background=False, status=409).json() == {"detail": TOGGLE_PENDING}
    assert toggle(client, pid, True, status=409).json() == {"detail": TOGGLE_PENDING}
    assert len(page_gens(app, pid)) == n and get_page(app, pid).include_character is False
    assert gen_row(app, fourth.id).status == GenStatus.RUNNING


def test_page_out_character_pending(client, app):
    b, pid, bg, pose = produced(client, app)
    assert page_out(client, b["id"], pid)["character_pending"] is False
    gen_background(client, app, pid, run=False)  # 一般的背景排隊中：不算切換中
    assert page_out(client, b["id"], pid)["character_pending"] is False
    run_jobs(client, app)
    drain(client, app)
    toggle(client, pid, False, status=202)
    [job] = active(app, pid)
    assert page_out(client, b["id"], pid)["character_pending"] is True
    set_status(app, job.id, GenStatus.RUNNING)
    assert page_out(client, b["id"], pid)["character_pending"] is True
    set_status(app, job.id, GenStatus.QUEUED)
    run_jobs(client, app)
    assert page_out(client, b["id"], pid)["character_pending"] is False


# ---- 復原、一定換上、自動清理 ---------------------------------------------------------


def test_undo_after_toggle_off(client, app):
    b, pid, old_bg, pose, new_bg = off_page(client, app)
    r = client.post(f"/api/pages/{pid}/undo")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["background_generation_id"] == old_bg and out["include_character"] is True
    drain(client, app)
    pg = get_page(app, pid)
    assert pg.status == PageStatus.PASSED and pg.include_character is True
    params = selected_params(app, pid)
    assert params["background_generation_id"] == old_bg and params["pose_generation_id"] == pose


def test_editor_toggle_takes_over_user_pick(client, app):
    """不帶 oneclick（進階編輯）也一定換上，連使用者自己選的背景也換掉。"""
    b, pid, bg, pose = produced(client, app)
    other = gen_background(client, app, pid)
    drain(client, app)
    assert client.post(f"/api/pages/{pid}/background", json={"generation_id": bg}).status_code == 200
    pg = get_page(app, pid)
    assert pg.background_generation_id == bg != pg.auto_background_generation_id  # 使用者選的
    toggle(client, pid, False, status=202)
    [job] = active(app, pid)
    assert wants_take(job)
    run_jobs(client, app)
    pg = get_page(app, pid)
    assert pg.background_generation_id == job.id == pg.auto_background_generation_id
    assert pg.include_character is False
    assert other != job.id


def test_toggle_on_auto_cleanup_page_flips_when_cleanup_lands(client, app):
    """auto_cleanup 頁：切換的背景好了先排自動清理（帶著 set_character），清好的換上時才改欄位；
    清理排隊中再切換是 409（背景已經畫好了）。"""
    b, pid, bg = bg_only_page(client, app)
    r = client.patch(f"/api/pages/{pid}", json={"auto_cleanup": True})
    assert r.status_code == 200, r.text
    owner = owner_of(app, b["id"])
    before = used(app, owner)
    toggle(client, pid, True, redraw_background=True, status=202)
    new_bg, new_pose = active(app, pid)
    assert used(app, owner) == before + 3  # 背景連自動清理 2 次＋角色動作 1 次
    run_jobs(client, app)  # 背景好了：排自動清理
    [cleanup] = [g for g in active(app, pid) if g.kind == GenKind.BACKGROUND]
    assert cleanup.source_generation_id == new_bg.id and set_character_of(cleanup) is True
    assert get_page(app, pid).include_character is False
    assert page_out(client, b["id"], pid)["character_pending"] is True
    assert toggle(client, pid, False, redraw_background=False, status=409).json() == {"detail": TOGGLE_PENDING}
    run_jobs(client, app, 2)
    drain(client, app)
    pg = get_page(app, pid)
    assert pg.include_character is True and pg.background_generation_id == cleanup.id
    assert pg.pose_generation_id == new_pose.id and pg.status == PageStatus.PASSED


# ---- 其他入口 -------------------------------------------------------------------------


def test_book_pose_redraw_skips_no_character(client, app):
    from tests.test_api_pages import new_page

    b, pid, bg, pose = produced(client, app)
    p2 = new_page(client, b["id"], scene_prompt="森林", action_prompt="跳")["id"]
    set_page(app, p2, include_character=False)
    owner = owner_of(app, b["id"])
    before = used(app, owner)
    r = client.post(f"/api/books/{b['id']}/poses/redraw", json={"page_ids": [pid, p2], "count": 1})
    assert r.status_code == 202, r.text
    out = r.json()
    assert [g["page_id"] for g in out["queued"]] == [pid]
    assert [(s["page_id"], s["reason"]) for s in out["skipped"]] == [(p2, NO_CHARACTER_SKIP)]
    assert used(app, owner) == before + 1
    assert page_gens(app, p2) == []


def test_patch_include_character_upload_recomposes(client, app):
    """設定頁改 include_character、背景是上傳的（不標要重畫）：排頁面流程，單獨重新合成背景。"""
    b, pid, bg, pose = produced(client, app)
    upload = upload_selected(client, app, pid)
    r = client.patch(f"/api/pages/{pid}", params={"from_setup": "true"}, json={"include_character": False})
    assert r.status_code == 200, r.text
    assert r.json()["redraw_background"] is False
    assert queued(app, pid)
    drain(client, app)
    pg = get_page(app, pid)
    assert pg.status == PageStatus.PASSED
    params = selected_params(app, pid)
    assert params["background_generation_id"] == upload and params["pose_generation_id"] is None
    # 加回來：連角色動作一起重新合成
    r = client.patch(f"/api/pages/{pid}", params={"from_setup": "true"}, json={"include_character": True})
    assert r.status_code == 200, r.text
    drain(client, app)
    assert selected_params(app, pid)["pose_generation_id"] == pose


def test_patch_include_character_ai_background_marks_redraw_without_queue(client, app):
    """AI 背景：照舊只標要重畫，不排工作（等「繼續做」）。"""
    b, pid, bg, pose = produced(client, app)
    r = client.patch(f"/api/pages/{pid}", params={"from_setup": "true"}, json={"include_character": False})
    assert r.status_code == 200 and r.json()["redraw_background"] is True
    assert not queued(app, pid) and active(app, pid) == []


def test_set_character_flag_roundtrip():
    from app.models import Generation as G
    from app.services.gen_request import encode_request

    rj = encode_request(kind=GenKind.BACKGROUND, prompt="p", negative_prompt="", seed=1, width=8, height=8,
                        extra={SET_CHARACTER: False})
    assert set_character_of(G(request_json=rj)) is False
    rj = encode_request(kind=GenKind.BACKGROUND, prompt="p", negative_prompt="", seed=1, width=8, height=8,
                        extra={SET_CHARACTER: True})
    assert set_character_of(G(request_json=rj)) is True
    assert set_character_of(G(request_json="{}")) is None
    assert set_character_of(G(request_json="not json")) is None
    assert set_character_of(G(request_json='{"set_character": 1}')) is None


# ---- 最終 review 修正 ----------------------------------------------------------------


def test_oneclick_strokes_on_bg_only_page_stay_passed_and_queue(client, app):
    """不放主角、沒有角色動作的頁：C 的畫筆照樣算頁面流程做的頁，不退回草稿、排流程補線。"""
    b, pid, bg = bg_only_page(client, app)
    stroke = {"op": "draw", "width_px": 12, "points": [[0.2, 0.5], [0.8, 0.5]]}
    gid = get_page(app, pid).selected_generation_id
    r = client.put(f"/api/pages/{pid}/lineart/edits", params=C, json={"generation_id": gid, "strokes": [stroke]})
    assert r.status_code == 200, r.text
    assert get_page(app, pid).status == PageStatus.PASSED
    assert queued(app, pid)


def test_on_pipeline_is_include_aware(client, app):
    from app.models import Page
    from app.services.page_lineart import on_pipeline

    b, pid, bg = bg_only_page(client, app)
    with app.state.sessions() as s:
        page = s.get(Page, pid)
        gen = s.get(Generation, page.selected_generation_id)
        assert on_pipeline(page, gen) is True
        page.include_character = True  # 要放主角的頁沒有角色動作：不算
        assert on_pipeline(page, gen) is False


def test_pose_landing_on_no_character_page_keeps_strokes():
    from app.models import Page
    from app.services.history import can_compose_after

    page = Page(book_id=1, background_generation_id=5, include_character=False)
    assert can_compose_after(page, GenKind.POSE) is False
    assert can_compose_after(page, GenKind.BACKGROUND) is True
    page.include_character = True
    page.pose_generation_id = 6
    assert can_compose_after(page, GenKind.POSE) is True


def test_retry_does_not_requeue_pose_on_no_character_page(client, app):
    from app.api.history import _failed_kinds

    b, pid, bg = bg_only_page(client, app)
    with app.state.sessions() as s:
        s.add(Generation(book_id=b["id"], page_id=pid, kind=GenKind.POSE, status=GenStatus.FAILED, user_text="p",
                         prompt="p", negative_prompt="", seed=1, width=8, height=8, request_json="{}"))
        s.commit()
        assert GenKind.POSE not in _failed_kinds(s, pid)
    set_page(app, pid, include_character=True)
    with app.state.sessions() as s:
        assert GenKind.POSE in _failed_kinds(s, pid)
