"""每個邀請每天的生成上限（Phase 10 Task 6）。

排隊時計數、FAILED／CANCELLED 退回（每筆只退一次）、日界線是 Asia/Taipei、
超過上限 429「今天的生成次數已用完，明天再來」；合成與自動選圖不計。admin 計數但不設上限。
"""

import json
import threading
from datetime import date, datetime, timezone

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlmodel import select

from app.config import load_settings
from app.db import init_db, make_engine, session_factory
from app.main import create_app
from app.models import Book, Generation, GenKind, GenStatus, Owner, Usage
from app.services import auth, usage
from app.services.engine import EngineError
from app.services.layout import PaperSize, PrintMode
from app.services.engine import RETIRED
from app.services.gen_request import encode_request
from app.services.worker import GenerationWorker
from app.storage import LocalStorage
from tests.fake_engine import FakeEngine
from tests.test_api_books import candidates, new_book, run_jobs
from tests.test_api_compose import confirm, gen_background, gen_pose, picked
from tests.test_api_lineart import color_png
from tests.test_api_pages import new_page

CAP_MSG = "今天的生成次數已用完，明天再來"
NOW = datetime(2026, 11, 11, 3, 0, tzinfo=timezone.utc)  # 台北 11/11 11:00
TODAY = date(2026, 11, 11)


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


def login(client, app, name="甲", *, cap=60, is_admin=False) -> int:
    with app.state.sessions() as s:
        owner, token = auth.create_owner(s, name, is_admin=is_admin, cap=cap)
    client.cookies.clear()
    r = client.get(f"/i/{token}", follow_redirects=False)
    assert r.status_code == 303
    return owner.id


def used(app, owner_id, day=TODAY) -> int:
    with app.state.sessions() as s:
        row = s.get(Usage, (owner_id, day))
        return 0 if row is None else row.generations


def set_cap(app, owner_id, cap):
    with app.state.sessions() as s:
        o = s.get(Owner, owner_id)
        o.daily_generation_cap = cap
        s.add(o)
        s.commit()


def gens(app, **where) -> list[Generation]:
    with app.state.sessions() as s:
        q = select(Generation)
        for k, v in where.items():
            q = q.where(getattr(Generation, k) == v)
        return list(s.exec(q).all())


def rj(app, gen_id) -> dict:
    with app.state.sessions() as s:
        return json.loads(s.get(Generation, gen_id).request_json)


# ---- 日界線 -----------------------------------------------------------------------


def test_taipei_midnight_boundary(tmp_path):
    old = datetime(2026, 11, 10, 15, 59, 59, tzinfo=timezone.utc)
    new = datetime(2026, 11, 10, 16, 0, 0, tzinfo=timezone.utc)
    assert usage.taipei_day(old) == date(2026, 11, 10)
    assert usage.taipei_day(new) == date(2026, 11, 11)
    assert usage.taipei_day(old.replace(tzinfo=None)) == date(2026, 11, 10)  # naive＝UTC（SQLite 讀回來的樣子）

    sessions, owner_id = _db(tmp_path, cap=1)
    with sessions() as s:
        owner = s.get(Owner, owner_id)
        assert usage.reserve(s, owner, 1, old) == date(2026, 11, 10)
        s.commit()
        with pytest.raises(HTTPException) as e:
            usage.reserve(s, owner, 1, old)
        assert e.value.status_code == 429 and e.value.detail == CAP_MSG
        s.rollback()
        assert usage.reserve(s, owner, 1, new) == date(2026, 11, 11)  # 新的一天重新計算
        s.commit()
        assert s.get(Usage, (owner_id, date(2026, 11, 10))).generations == 1
        assert s.get(Usage, (owner_id, date(2026, 11, 11))).generations == 1


# ---- 上限 ---------------------------------------------------------------------------


def test_cap_429_at_61st(client, app):
    owner_id = login(client, app)  # 預設上限 60
    b = new_book(client)
    for _ in range(60):
        assert candidates(client, b["id"]).status_code == 202
    r = candidates(client, b["id"])
    assert r.status_code == 429
    assert r.json() == {"detail": CAP_MSG}
    assert len(gens(app, book_id=b["id"])) == 60  # 第 61 筆沒有排進去
    assert used(app, owner_id) == 60


def test_every_enqueue_route_is_counted_and_capped(client, app):
    owner_id = login(client, app, cap=3)
    app.state.engine.image = color_png()
    b = new_book(client)
    gid = candidates(client, b["id"]).json()[0]["id"]
    run_jobs(client, app)
    assert client.post(f"/api/books/{b['id']}/character", json={"generation_id": gid}).status_code == 200
    p = new_page(client, b["id"], scene_prompt="公園", action_prompt="揮手")
    gen_background(client, app, p["id"], run=False)
    gen_pose(client, app, p["id"], run=False)
    new_page(client, b["id"], scene_prompt="海邊", action_prompt="跑")  # 讓批次有東西可排
    assert used(app, owner_id) == 3
    for r in (
        client.post(f"/api/pages/{p['id']}/backgrounds"),
        client.post(f"/api/pages/{p['id']}/poses", json={"action": "跳"}),
        candidates(client, new_book(client)["id"]),
        client.post(f"/api/books/{b['id']}/materials/batch", json={}),
    ):
        assert r.status_code == 429, r.text
        assert r.json()["detail"] == CAP_MSG
    assert used(app, owner_id) == 3


def test_generation_records_usage_day_and_owner(client, app):
    owner_id = login(client, app)
    b = new_book(client)
    gid = candidates(client, b["id"]).json()[0]["id"]
    d = rj(app, gid)
    assert d["usage_day"] == "2026-11-11"
    assert d["usage_owner"] == owner_id
    assert "usage_refunded" not in d


def test_admin_is_counted_but_uncapped(client, app):
    owner_id = login(client, app, "管理者", cap=1, is_admin=True)
    b = new_book(client)
    for _ in range(3):
        assert candidates(client, b["id"]).status_code == 202
    assert used(app, owner_id) == 3


# ---- 退回 ---------------------------------------------------------------------------


def test_failed_and_cancelled_refund(client, app):
    owner_id = login(client, app, cap=2)
    b = new_book(client)
    first = candidates(client, b["id"]).json()[0]["id"]
    second = candidates(client, b["id"]).json()[0]["id"]
    assert candidates(client, b["id"]).status_code == 429

    # 取消（QUEUED → CANCELLED）退回一次
    assert client.post(f"/api/generations/{second}/cancel").status_code == 200
    assert used(app, owner_id) == 1
    assert rj(app, second)["usage_refunded"] is True
    assert client.post(f"/api/generations/{second}/cancel").status_code == 409
    assert client.patch(f"/api/generations/{second}", json={"discarded": True}).status_code == 200
    assert used(app, owner_id) == 1  # 不會退兩次

    # 失敗（FAILED）退回
    app.state.engine.outcomes.append(EngineError("boom"))
    run_jobs(client, app)
    assert gens(app, id=first)[0].status == GenStatus.FAILED
    assert used(app, owner_id) == 0
    assert rj(app, first)["usage_refunded"] is True
    assert client.patch(f"/api/generations/{first}", json={"discarded": True}).status_code == 200
    assert used(app, owner_id) == 0

    # 失敗後重新產生＝新的一筆，照常計數
    assert candidates(client, b["id"]).status_code == 202
    assert candidates(client, b["id"]).status_code == 202
    assert candidates(client, b["id"]).status_code == 429


def test_success_is_not_refunded(client, app):
    owner_id = login(client, app)
    b = new_book(client)
    candidates(client, b["id"])
    run_jobs(client, app)
    assert used(app, owner_id) == 1


def test_discarding_queued_refunds(client, app):
    owner_id = login(client, app)
    b = new_book(client)
    gid = candidates(client, b["id"]).json()[0]["id"]
    assert client.patch(f"/api/generations/{gid}", json={"discarded": True}).status_code == 200
    assert gens(app, id=gid)[0].status == GenStatus.CANCELLED
    assert used(app, owner_id) == 0


def test_choosing_character_refunds_other_queued_candidates(client, app):
    owner_id = login(client, app)
    b = new_book(client)
    gid = candidates(client, b["id"]).json()[0]["id"]
    run_jobs(client, app)
    candidates(client, b["id"])
    candidates(client, b["id"])
    assert used(app, owner_id) == 3
    assert client.post(f"/api/books/{b['id']}/character", json={"generation_id": gid}).status_code == 200
    assert used(app, owner_id) == 1


def test_deleting_page_refunds_its_queued_jobs(client, app):
    owner_id = login(client, app)
    app.state.engine.image = color_png()
    b = new_book(client)
    gid = candidates(client, b["id"]).json()[0]["id"]
    run_jobs(client, app)
    client.post(f"/api/books/{b['id']}/character", json={"generation_id": gid})
    p = new_page(client, b["id"], scene_prompt="公園")
    gen_background(client, app, p["id"], run=False)
    gen_pose(client, app, p["id"], run=False)
    assert used(app, owner_id) == 3
    assert client.delete(f"/api/pages/{p['id']}").status_code == 204
    assert used(app, owner_id) == 1


def test_refund_hits_the_recorded_day(client, app, monkeypatch):
    owner_id = login(client, app)
    b = new_book(client)
    gid = candidates(client, b["id"]).json()[0]["id"]
    monkeypatch.setattr(usage, "clock", lambda: datetime(2026, 11, 12, 3, 0, tzinfo=timezone.utc))
    assert client.post(f"/api/generations/{gid}/cancel").status_code == 200
    assert used(app, owner_id, TODAY) == 0
    assert used(app, owner_id, date(2026, 11, 12)) == 0


def test_refund_never_goes_negative(tmp_path):
    sessions, owner_id = _db(tmp_path)
    with sessions() as s:
        usage.refund(s, owner_id, TODAY)  # 沒有這一天的列：什麼都不做
        s.add(Usage(owner_id=owner_id, day=TODAY, generations=1))
        s.commit()
        usage.refund(s, owner_id, TODAY, n=3)
        s.commit()
        assert s.get(Usage, (owner_id, TODAY)).generations == 0


# ---- 批次 ---------------------------------------------------------------------------


def test_batch_reserves_n_atomically(client, app):
    owner_id = login(client, app, cap=4)
    app.state.engine.image = color_png()
    b = new_book(client)
    gid = candidates(client, b["id"]).json()[0]["id"]
    run_jobs(client, app)
    client.post(f"/api/books/{b['id']}/character", json={"generation_id": gid})
    for _ in range(2):
        new_page(client, b["id"], scene_prompt="公園", action_prompt="揮手")
    before = len(gens(app, book_id=b["id"]))
    # 2 頁 × (背景＋動作) = 4，只剩 3：整批不排
    r = client.post(f"/api/books/{b['id']}/materials/batch", json={})
    assert r.status_code == 429
    assert r.json()["detail"] == "今天只剩 3 次，這次需要 4 次"  # 使用者決定：整批擋下、說清楚
    assert len(gens(app, book_id=b["id"])) == before
    assert used(app, owner_id) == 1

    set_cap(app, owner_id, 5)
    r = client.post(f"/api/books/{b['id']}/materials/batch", json={})
    assert r.status_code == 202, r.text
    assert len(r.json()["queued"]) == 4
    assert used(app, owner_id) == 5
    queued = gens(app, book_id=b["id"], status=GenStatus.QUEUED)
    assert len(queued) == 4
    assert all(json.loads(g.request_json)["usage_day"] == "2026-11-11" for g in queued)

    # 沒有東西要排：不計數，也不會因為額度用完而 429
    r = client.post(f"/api/books/{b['id']}/materials/batch", json={})
    assert r.status_code == 202
    assert r.json()["queued"] == []
    assert used(app, owner_id) == 5


# ---- 不計數 ---------------------------------------------------------------------------


def test_compose_not_counted(client, app):
    owner_id = login(client, app, cap=3)
    app.state.engine.image = color_png()
    b = new_book(client)
    gid = candidates(client, b["id"]).json()[0]["id"]
    run_jobs(client, app)
    client.post(f"/api/books/{b['id']}/character", json={"generation_id": gid})
    p = new_page(client, b["id"], scene_prompt="公園")
    bg = gen_background(client, app, p["id"])  # 跑完會自動選用（autoselect）
    pose = gen_pose(client, app, p["id"])
    assert used(app, owner_id) == 3
    picked(client, p["id"], "background", bg)
    picked(client, p["id"], "pose", pose)
    page = confirm(client, p["id"])
    assert page["selected_generation_id"] is not None
    up = client.post(
        f"/api/pages/{p['id']}/backgrounds/upload", files={"file": ("bg.png", color_png(), "image/png")}
    )
    assert up.status_code == 201, up.text
    assert used(app, owner_id) == 3


# ---- reserve 的原子性 -----------------------------------------------------------------


def _db(tmp_path, cap=60):
    engine = make_engine(tmp_path / "u.db")
    init_db(engine)
    sessions = session_factory(engine)
    with sessions() as s:
        o = Owner(name="t", token_hash="h" * 64, daily_generation_cap=cap)
        s.add(o)
        s.commit()
        return sessions, o.id


@pytest.mark.parametrize("start", [None, 4], ids=["first-insert", "at-cap-minus-1"])
def test_concurrent_reserve_never_exceeds_cap(tmp_path, start):
    cap = 1 if start is None else 5
    sessions, owner_id = _db(tmp_path, cap=cap)
    if start is not None:
        with sessions() as s:
            s.add(Usage(owner_id=owner_id, day=TODAY, generations=start))
            s.commit()
    barrier = threading.Barrier(2)
    results: list[object] = []

    def worker():
        with sessions() as s:
            owner = s.get(Owner, owner_id)
            barrier.wait()
            try:
                usage.reserve(s, owner, 1, NOW)
                s.commit()
                results.append("ok")
            except HTTPException as e:
                s.rollback()
                results.append(e.status_code)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert sorted(results, key=str) == [429, "ok"]
    with sessions() as s:
        assert s.get(Usage, (owner_id, TODAY)).generations == cap


def test_reserve_n_over_cap_writes_nothing(tmp_path):
    sessions, owner_id = _db(tmp_path, cap=3)
    with sessions() as s:
        owner = s.get(Owner, owner_id)
        with pytest.raises(HTTPException):
            usage.reserve(s, owner, 4, NOW)  # 一次就超過：連第一列都不建
        s.rollback()
        assert s.get(Usage, (owner_id, TODAY)) is None
        usage.reserve(s, owner, 2, NOW)
        s.commit()
        with pytest.raises(HTTPException):
            usage.reserve(s, owner, 2, NOW)
        s.rollback()
        assert s.get(Usage, (owner_id, TODAY)).generations == 2



def test_reserve_batch_over_remaining_says_how_many_left(tmp_path):
    # 使用者決定（2026-11-11）：批次超過今天剩下的次數 → 整批擋下，訊息說清楚剩幾次、需要幾次；
    # 剩 0 次時維持「今天的生成次數已用完，明天再來」
    sessions, owner_id = _db(tmp_path, cap=5)
    with sessions() as s:
        owner = s.get(Owner, owner_id)
        with pytest.raises(HTTPException) as e:
            usage.reserve(s, owner, 7, NOW)  # 一列都還沒有：剩 5 次
        assert e.value.status_code == 429 and e.value.detail == "今天只剩 5 次，這次需要 7 次"
        s.rollback()
        usage.reserve(s, owner, 2, NOW)
        s.commit()
        with pytest.raises(HTTPException) as e:
            usage.reserve(s, owner, 4, NOW)
        assert e.value.status_code == 429 and e.value.detail == "今天只剩 3 次，這次需要 4 次"
        s.rollback()
        assert s.get(Usage, (owner_id, TODAY)).generations == 2
        usage.reserve(s, owner, 3, NOW)
        s.commit()
        for n in (1, 4):  # 用完了：不論要幾次都是原本的訊息
            with pytest.raises(HTTPException) as e:
                usage.reserve(s, owner, n, NOW)
            assert e.value.detail == usage.DAILY_CAP_REACHED
            s.rollback()
        assert s.get(Usage, (owner_id, TODAY)).generations == 5

# ---- worker 的退回路徑 ------------------------------------------------------------------


@pytest.fixture
def wenv(tmp_path):
    sessions, owner_id = _db(tmp_path)
    with sessions() as s:
        s.add(Book(title="b", paper_size=PaperSize.A4, print_mode=PrintMode.HOME, owner_id=owner_id))
        s.add(Usage(owner_id=owner_id, day=TODAY, generations=10))
        s.commit()
    engine = FakeEngine()
    worker = GenerationWorker(engine, sessions, LocalStorage(tmp_path / "st", b"k"), retry_backoff_s=0.01)
    return worker, engine, sessions, owner_id


def _counted_gen(sessions, owner_id, *, status=GenStatus.QUEUED, request_json=None, extra=None) -> int:
    rj_ = request_json or encode_request(
        kind=GenKind.CHARACTER, prompt="p", negative_prompt="", seed=1, width=64, height=64,
        extra={"usage_day": TODAY.isoformat(), "usage_owner": owner_id, **(extra or {})},
    )
    with sessions() as s:
        g = Generation(book_id=1, kind=GenKind.CHARACTER, user_text="u", prompt="p", negative_prompt="",
                       seed=1, width=64, height=64, request_json=rj_, status=status)
        s.add(g)
        s.commit()
        return g.id


def _used(sessions, owner_id) -> int:
    with sessions() as s:
        return s.get(Usage, (owner_id, TODAY)).generations


async def test_worker_failure_refunds_once(wenv):
    worker, engine, sessions, owner_id = wenv
    gid = _counted_gen(sessions, owner_id)
    engine.outcomes.append(EngineError("boom"))
    assert await worker.run_once()
    assert _used(sessions, owner_id) == 9
    worker._fail(gid, "again")  # 已不是 RUNNING：不更新，也不再退
    assert _used(sessions, owner_id) == 9


async def test_worker_retired_and_parse_error_refund(wenv):
    worker, engine, sessions, owner_id = wenv
    _counted_gen(sessions, owner_id, extra={"pose_mode": "fast"})  # 已停用的路線
    _counted_gen(sessions, owner_id)
    with sessions() as s:  # 第二筆改成 decode 會失敗（缺 prompt），但仍帶 usage_day
        g = s.exec(select(Generation).order_by(Generation.id.desc())).first()
        d = json.loads(g.request_json)
        del d["prompt"]
        g.request_json = json.dumps(d)
        s.add(g)
        s.commit()
    assert await worker.run_once()
    assert await worker.run_once()
    with sessions() as s:
        rows = s.exec(select(Generation)).all()
    assert [r.status for r in rows] == [GenStatus.FAILED, GenStatus.FAILED]
    assert rows[0].error == RETIRED
    assert _used(sessions, owner_id) == 8


def test_cancel_retired_refunds(wenv):
    worker, engine, sessions, owner_id = wenv
    _counted_gen(sessions, owner_id, extra={"background_style": "detailed"})
    _counted_gen(sessions, owner_id)
    assert worker.cancel_retired() == 1
    assert _used(sessions, owner_id) == 9
    assert worker.cancel_retired() == 0
    assert _used(sessions, owner_id) == 9


async def test_requeued_then_failed_refunds_once(wenv):
    worker, engine, sessions, owner_id = wenv
    _counted_gen(sessions, owner_id, status=GenStatus.RUNNING)
    assert worker.requeue_interrupted() == 1
    assert _used(sessions, owner_id) == 10  # 重新排隊不退
    engine.outcomes.append(EngineError("boom"))
    assert await worker.run_once()
    assert _used(sessions, owner_id) == 9


async def test_unparseable_request_json_fails_without_refund(wenv):
    worker, engine, sessions, owner_id = wenv
    _counted_gen(sessions, owner_id, request_json="{not json")
    assert await worker.run_once()
    assert _used(sessions, owner_id) == 10  # 讀不到 usage_day：無從退回，也不會讓 worker 出錯


async def test_legacy_rows_without_usage_day_not_refunded(wenv):
    worker, engine, sessions, owner_id = wenv
    rj_ = encode_request(kind=GenKind.CHARACTER, prompt="p", negative_prompt="", seed=1, width=64, height=64)
    _counted_gen(sessions, owner_id, request_json=rj_)
    engine.outcomes.append(EngineError("boom"))
    assert await worker.run_once()
    assert _used(sessions, owner_id) == 10


# ---- 只預留放得下的（Phase 13 /produce）------------------------------------------------


def test_reserve_up_to_grants_what_is_left(tmp_path):
    sessions, owner_id = _db(tmp_path, cap=5)
    with sessions() as s:
        owner = s.get(Owner, owner_id)
        assert usage.remaining(s, owner, NOW) == 5
        assert usage.reserve_up_to(s, owner, 3, NOW) == (TODAY, 3)
        s.commit()
        assert usage.remaining(s, owner, NOW) == 2
        assert usage.reserve_up_to(s, owner, 4, NOW) == (TODAY, 2)  # 只剩 2：給 2
        s.commit()
        assert usage.remaining(s, owner, NOW) == 0
        with pytest.raises(HTTPException) as e:
            usage.reserve_up_to(s, owner, 1, NOW)
        assert e.value.status_code == 429 and e.value.detail == usage.DAILY_CAP_REACHED
        s.rollback()
        assert s.get(Usage, (owner_id, TODAY)).generations == 5


def test_reserve_up_to_admin_is_uncapped(tmp_path):
    sessions, owner_id = _db(tmp_path, cap=1)
    with sessions() as s:
        owner = s.get(Owner, owner_id)
        owner.is_admin = True
        s.add(owner)
        s.commit()
        assert usage.remaining(s, owner, NOW) is None
        assert usage.reserve_up_to(s, owner, 7, NOW) == (TODAY, 7)
        s.commit()
        assert s.get(Usage, (owner_id, TODAY)).generations == 7


def test_concurrent_reserve_up_to_never_exceeds_cap(tmp_path):
    sessions, owner_id = _db(tmp_path, cap=4)
    barrier = threading.Barrier(2)
    granted: list[object] = []

    def worker():
        with sessions() as s:
            owner = s.get(Owner, owner_id)
            barrier.wait()
            try:
                granted.append(usage.reserve_up_to(s, owner, 3, NOW)[1])
                s.commit()
            except HTTPException as e:
                s.rollback()
                granted.append(e.status_code)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert sorted(granted) == [1, 3]
    with sessions() as s:
        assert s.get(Usage, (owner_id, TODAY)).generations == 4
