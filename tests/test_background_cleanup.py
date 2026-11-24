"""「清掉框外雜物」：POST /api/pages/{id}/backgrounds/cleanup 用這頁目前選用的背景排一筆清理
（qwen_edit_cleanup；指令＝頁面的 cleanup_prompt 或 GENERIC），計入當日次數，take 規則同「換一張」。"""

import json

import pytest
from fastapi.testclient import TestClient
from sqlmodel import col, select

from app.config import load_settings
from app.main import create_app
from app.models import Generation, GenKind, GenStatus, Owner, PageHistory, PageStatus, Usage
from app.services import auth, usage
from app.services.gen_request import is_cleanup, wants_take
from app.services.prompts import GENERIC_CLEANUP_PROMPT
from tests.fake_engine import FakeEngine
from tests.test_api_books import run_jobs
from tests.test_api_compose import gen_row, png_bytes
from tests.test_api_lineart import get_page
from tests.test_pipeline import book_page, drain, produced

C = {"oneclick": "true"}
CAP_MSG = "今天的生成次數已用完，明天再來"
NOT_PORTRAIT = "有框的背景（肖像模式）才能清"
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
        from app.models import Book
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


def portrait(client, pid):
    r = client.put(f"/api/pages/{pid}/placement", json=PORTRAIT)
    assert r.status_code == 200, r.text


def cleanup(client, pid, *, status=202, **params):
    r = client.post(f"/api/pages/{pid}/backgrounds/cleanup", params=params)
    assert r.status_code == status, r.text
    return r.json()


def page_gens(app, pid, kind=GenKind.BACKGROUND) -> list[Generation]:
    with app.state.sessions() as s:
        return list(s.exec(select(Generation).where(Generation.page_id == pid, Generation.kind == kind)
                           .order_by(col(Generation.id))).all())


def history_count(app, pid) -> int:
    with app.state.sessions() as s:
        return len(s.exec(select(PageHistory).where(PageHistory.page_id == pid)).all())


def portrait_page(client, app):
    """背景＋角色動作產生完、流程跑完，頁面改成肖像模式（位置帶 frame）。"""
    b, p, bg, pose = produced(client, app)
    portrait(client, p["id"])
    return b, p, bg, pose


# ---- 排一筆清理 -------------------------------------------------------------------


def test_cleanup_queues_generic_cleanup_of_selected_background(client, app):
    b, p, bg, pose = portrait_page(client, app)
    pid = p["id"]
    owner_id = owner_of(app, b["id"])
    before = used(app, owner_id)
    src = gen_row(app, bg)

    out = cleanup(client, pid)
    assert out["kind"] == "background" and out["status"] == "queued"
    assert out["source_generation_id"] == bg and out["user_text"] == ""
    assert [out["width"], out["height"]] == [src.width, src.height]
    row = gen_row(app, out["id"])
    assert row.prompt == GENERIC_CLEANUP_PROMPT and row.negative_prompt == ""
    assert is_cleanup(row) and not wants_take(row)
    rj = json.loads(row.request_json)
    assert rj["cleanup"] is True and "background_style" not in rj
    assert used(app, owner_id) == before + 1  # 與「換一張」一樣算 1 次

    run_jobs(client, app)
    req = app.state.engine.requests[-1]
    assert req.kind == GenKind.BACKGROUND
    assert req.source_image == app.state.storage.get(src.image_path)
    assert req.prompt == GENERIC_CLEANUP_PROMPT
    pg = get_page(app, pid)  # 原本的背景是自動選的：清好的換上去（同新背景的自動選圖）
    assert pg.background_generation_id == out["id"] == pg.auto_background_generation_id
    drain(client, app)
    assert get_page(app, pid).status == PageStatus.PASSED


def test_cleanup_uses_page_cleanup_prompt(client, app):
    b, p, bg, pose = portrait_page(client, app)
    pid = p["id"]
    mine = "Delete the clover border. Keep the frame."
    r = client.patch(f"/api/pages/{pid}", json={"cleanup_prompt": mine})
    assert r.status_code == 200, r.text
    out = cleanup(client, pid)
    assert gen_row(app, out["id"]).prompt == mine


def upload_selected(client, app, pid) -> int:
    r = client.post(f"/api/pages/{pid}/backgrounds/upload", params=C,
                    files={"file": ("mine.png", png_bytes(), "image/png")})
    assert r.status_code == 201, r.text
    drain(client, app)
    return get_page(app, pid).background_generation_id


def test_cleanup_replaces_user_picked_source_and_keeps_it_as_thumbnail(client, app):
    """舊編輯器（不帶 oneclick）：選用中的背景還是清理的來源，好了就換上（使用者上傳／選的也換），
    舊的那張留在縮圖；仍算使用者選的（auto_* 不動），換之前存快照，重新合成。"""
    b, p, bg, pose = portrait_page(client, app)
    pid = p["id"]
    upload = upload_selected(client, app, pid)
    assert upload != bg
    auto_before = get_page(app, pid).auto_background_generation_id
    composite_before = get_page(app, pid).selected_generation_id
    n = history_count(app, pid)

    out = cleanup(client, pid)
    assert out["source_generation_id"] == upload and not wants_take(gen_row(app, out["id"]))
    run_jobs(client, app)
    pg = get_page(app, pid)
    assert pg.background_generation_id == out["id"]
    assert pg.auto_background_generation_id == auto_before  # 來源是使用者選的：清好的也算使用者選的
    assert history_count(app, pid) == n + 1  # 換上去之前存快照
    old = gen_row(app, upload)
    assert old.status == GenStatus.SUCCEEDED and not old.discarded  # 舊的那張留在縮圖
    drain(client, app)
    pg = get_page(app, pid)
    assert pg.status == PageStatus.PASSED and pg.selected_generation_id != composite_before  # 重新合成
    assert pg.background_generation_id == out["id"]


def test_cleanup_of_auto_source_stays_auto(client, app):
    b, p, bg, pose = portrait_page(client, app)
    pid = p["id"]
    assert get_page(app, pid).auto_background_generation_id == bg
    out = cleanup(client, pid)
    run_jobs(client, app)
    pg = get_page(app, pid)
    assert pg.background_generation_id == out["id"] == pg.auto_background_generation_id


def test_cleanup_does_not_override_selection_changed_meanwhile(client, app):
    """清理排隊時使用者改選了別張：好了不換掉（清好的那張留在縮圖）。"""
    from tests.test_api_compose import gen_background

    b, p, bg, pose = portrait_page(client, app)
    pid = p["id"]
    other = gen_background(client, app, pid)  # 自動換上 other
    drain(client, app)
    assert client.post(f"/api/pages/{pid}/background", json={"generation_id": bg}).status_code == 200
    drain(client, app)
    out = cleanup(client, pid)
    assert out["source_generation_id"] == bg
    assert client.post(f"/api/pages/{pid}/background", json={"generation_id": other}).status_code == 200
    n = history_count(app, pid)
    run_jobs(client, app)
    drain(client, app)
    pg = get_page(app, pid)
    assert pg.background_generation_id == other and history_count(app, pid) == n
    assert gen_row(app, out["id"]).status == GenStatus.SUCCEEDED


def test_cleanup_oneclick_snapshots_replaces_upload_and_recomposes(client, app):
    """oneclick（C）：上傳的背景也可以清；好了一定換上去並記成 auto_*（換之前存快照），重新合成。"""
    b, p, bg, pose = portrait_page(client, app)
    pid = p["id"]
    upload = upload_selected(client, app, pid)
    composite_before = get_page(app, pid).selected_generation_id
    n = history_count(app, pid)
    out = cleanup(client, pid, **C)
    assert out["source_generation_id"] == upload and wants_take(gen_row(app, out["id"]))
    run_jobs(client, app)
    pg = get_page(app, pid)
    assert pg.background_generation_id == out["id"] == pg.auto_background_generation_id
    assert history_count(app, pid) == n + 1  # 換上去之前存快照
    drain(client, app)
    pg = get_page(app, pid)
    assert pg.status == PageStatus.PASSED and pg.selected_generation_id != composite_before  # 重新合成
    r = client.post(f"/api/pages/{pid}/undo")
    assert r.status_code == 200 and r.json()["background_generation_id"] == upload


# ---- 不能清 -------------------------------------------------------------------------


def test_cleanup_without_selected_background_is_409(client, app):
    b, p = book_page(client, app)
    pid = p["id"]
    portrait(client, pid)
    owner_id = owner_of(app, b["id"])
    before = used(app, owner_id)
    assert cleanup(client, pid, status=409) == {"detail": "請先選好背景"}
    assert page_gens(app, pid) == [] and used(app, owner_id) == before


def test_cleanup_of_unusable_background_is_409(client, app):
    b, p, bg, pose = portrait_page(client, app)
    pid = p["id"]
    with app.state.sessions() as s:  # 選用中的背景不能用了（例如圖檔紀錄不見）
        g = s.get(Generation, bg)
        g.image_path = None
        s.add(g)
        s.commit()
    n = len(page_gens(app, pid))
    assert cleanup(client, pid, status=409) == {"detail": "請先選好背景"}
    assert len(page_gens(app, pid)) == n


def test_cleanup_not_portrait_is_409(client, app):
    b, p, bg, pose = produced(client, app)
    pid = p["id"]
    owner_id = owner_of(app, b["id"])
    before = used(app, owner_id)
    n = len(page_gens(app, pid))
    assert cleanup(client, pid, status=409) == {"detail": NOT_PORTRAIT}  # 沒有存過位置
    r = client.put(f"/api/pages/{pid}/placement", json={**PORTRAIT, "frame": None, "height": 0.8})
    assert r.status_code == 200, r.text
    assert cleanup(client, pid, status=409) == {"detail": NOT_PORTRAIT}  # 一般擺放
    assert len(page_gens(app, pid)) == n and used(app, owner_id) == before


def test_cleanup_quota_exhausted_is_429(client, app):
    b, p, bg, pose = portrait_page(client, app)
    pid = p["id"]
    owner_id = owner_of(app, b["id"])
    set_cap(app, owner_id, used(app, owner_id))
    n = len(page_gens(app, pid))
    assert cleanup(client, pid, status=429) == {"detail": CAP_MSG}
    assert len(page_gens(app, pid)) == n


def test_cleanup_other_owners_page_is_404(client, app):
    b, p, bg, pose = portrait_page(client, app)
    pid = p["id"]
    n = len(page_gens(app, pid))
    login(client, app, "乙")
    assert cleanup(client, pid, status=404) == {"detail": "找不到這一頁"}
    assert cleanup(client, 987654, status=404) == {"detail": "找不到這一頁"}
    assert len(page_gens(app, pid)) == n


def test_failed_cleanup_keeps_background_and_shows_error(client, app):
    """按鈕的清理失敗：原本的背景維持選用、頁面不設 failed（錯誤記在那筆 generation 上），次數退回。"""
    from app.services.engine import EngineError

    b, p, bg, pose = portrait_page(client, app)
    pid = p["id"]
    owner_id = owner_of(app, b["id"])
    before = used(app, owner_id)
    attention = get_page(app, pid).attention
    app.state.engine.outcomes.append(EngineError("ComfyUI 執行失敗：記憶體不足"))
    out = cleanup(client, pid, **C)
    run_jobs(client, app)
    row = gen_row(app, out["id"])
    assert row.status == GenStatus.FAILED and row.error
    pg = get_page(app, pid)
    assert pg.background_generation_id == bg and pg.attention == attention != "failed"
    assert used(app, owner_id) == before  # 失敗退回
