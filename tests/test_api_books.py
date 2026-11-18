import json

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from app.config import load_settings
from app.main import create_app
from app.models import Generation, GenKind, GenStatus
from app.services import prompts
from app.services.prompts import QWEN_CHARACTER_SIZE, qwen_character_prompt
from app.services.gen_request import encode_request
from tests.fake_engine import FakeEngine


@pytest.fixture
def app(settings_file):
    return create_app(load_settings(settings_file), engine=FakeEngine(), start_worker=False)


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


def run_jobs(client, app, n=1):
    for _ in range(n):
        assert client.portal.call(app.state.worker.run_once)


def new_book(client, **kw):
    body = {"title": "小熊", "paper_size": "a4", "print_mode": "print_shop", **kw}
    r = client.post("/api/books", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def candidates(client, book_id, **kw):
    return client.post(f"/api/books/{book_id}/character/candidates",
                       json={"description": "一隻戴帽子的小熊", **kw})


def test_create_and_get_book_with_layout(client):
    b = new_book(client)
    assert b["layout"]["generation_size"] == [864, 1216]
    assert b["layout"]["content_px"] == [2551, 3579]
    assert client.get(f"/api/books/{b['id']}").json() == b
    assert client.post("/api/books", json={"title": "x", "paper_size": "A0",
                                           "print_mode": "home"}).status_code == 422
    assert client.post("/api/books", json={"title": "", "paper_size": "a4",
                                           "print_mode": "home"}).status_code == 422


def test_create_book_assigns_admin_owner(client, app):
    """沒有 cookie 時是 auth.dev_owner（測試預設 admin）：新書都歸給那個 admin owner（沒有就建立一個）。"""
    from app.models import Book, Owner

    a, b = new_book(client, title="一"), new_book(client, title="二")
    with app.state.sessions() as s:
        owners = s.exec(select(Owner)).all()
        assert len(owners) == 1 and owners[0].is_admin and owners[0].name == "admin"
        assert {s.get(Book, x["id"]).owner_id for x in (a, b)} == {owners[0].id}


def test_list_patch_and_404(client):
    a, b = new_book(client, title="一"), new_book(client, title="二")
    assert [x["id"] for x in client.get("/api/books").json()] == [b["id"], a["id"]]
    r = client.patch(f"/api/books/{a['id']}", json={"title": "新名"})
    assert r.status_code == 200 and r.json()["title"] == "新名"
    assert r.json()["updated_at"] >= a["updated_at"]
    assert client.get("/api/books/999").status_code == 404
    assert client.patch("/api/books/999", json={"title": "x"}).status_code == 404
    assert candidates(client, 999).status_code == 404
    assert client.post("/api/books/999/character", json={"generation_id": 1}).status_code == 404


def candidate_ids(client, book_id, n, **kw):
    """一次一張：連按 n 次「產生角色」，回傳 generation id。"""
    out = []
    for _ in range(n):
        r = candidates(client, book_id, **kw)
        assert r.status_code == 202, r.text
        out += [g["id"] for g in r.json()]
    return out


def _variation(app, gid):
    with app.state.sessions() as s:
        return json.loads(s.get(Generation, gid).request_json)["variation"]


def _row(app, gid):
    with app.state.sessions() as s:
        return s.get(Generation, gid)


def test_character_candidates_one_image_per_click(client, app):
    b = new_book(client)
    r = candidates(client, b["id"], seed=100)
    assert r.status_code == 202
    gens = r.json()
    assert len(gens) == 1
    g = gens[0]
    assert g["status"] == "queued" and g["kind"] == "character"
    assert (g["width"], g["height"]) == QWEN_CHARACTER_SIZE
    row = _row(app, g["id"])
    assert row.seed == 100 and "一隻戴帽子的小熊" in row.prompt
    assert g["queue_position"] == 0 and g["image_url"] is None and g["progress"] is None
    with app.state.sessions() as s:
        row = s.get(Generation, g["id"])
        assert row.negative_prompt == ""
        assert json.loads(row.request_json)["variation"] in prompts.QWEN_CHARACTER_VARIATIONS
    # 舊前端會送 count：接受但忽略，一律一張
    for count in (4, 1, 8):
        r = candidates(client, b["id"], count=count)
        assert r.status_code == 202 and len(r.json()) == 1


def test_candidates_stored_fields_and_random_seed(client, app):
    b = new_book(client)
    gid = candidates(client, b["id"]).json()[0]["id"]
    with app.state.sessions() as s:
        g = s.get(Generation, gid)
        d = json.loads(g.request_json)
        v = d["variation"]
        assert g.user_text == "一隻戴帽子的小熊"
        assert v in prompts.QWEN_CHARACTER_VARIATIONS
        assert g.prompt == qwen_character_prompt("一隻戴帽子的小熊", v)
        assert g.negative_prompt == ""
        assert g.request_json == encode_request(
            kind=GenKind.CHARACTER, prompt=g.prompt, negative_prompt="",
            seed=g.seed, width=QWEN_CHARACTER_SIZE[0], height=QWEN_CHARACTER_SIZE[1], variation=v,
            extra={"usage_day": d["usage_day"], "usage_owner": d["usage_owner"]})  # 每日上限的計數紀錄
        assert 0 <= g.seed <= 2**32 - 1


def test_character_variation_never_repeats_consecutively(client, app):
    b = new_book(client)
    seen = [_variation(app, i) for i in candidate_ids(client, b["id"], 8)]
    assert all(v in prompts.QWEN_CHARACTER_VARIATIONS for v in seen)
    assert all(x != y for x, y in zip(seen, seen[1:])), seen


def test_character_variation_is_read_after_the_write_lock(client, app):
    """連按「再產生」：上一張的變化語要在取得寫入鎖（條件式 UPDATE books）之後才讀，
    第二個請求才看得到第一個寫入的變化語（不重複）。"""
    from sqlalchemy import event

    b = new_book(client)
    with app.state.sessions() as s:
        engine = s.get_bind()
    statements: list[str] = []

    def record(conn, cursor, statement, *a):
        statements.append(" ".join(statement.split()).lower())

    event.listen(engine, "before_cursor_execute", record)
    try:
        assert candidates(client, b["id"]).status_code == 202
    finally:
        event.remove(engine, "before_cursor_execute", record)
    lock = next(i for i, s in enumerate(statements) if s.startswith("update book "))
    reads = [i for i, s in enumerate(statements) if s.startswith("select") and "from generation " in s]
    assert reads and min(reads) > lock, statements


def test_character_variation_exclusion_is_per_book(client, app, monkeypatch):
    from app.api import books as books_mod

    b = new_book(client)
    last = _variation(app, candidates(client, b["id"]).json()[0]["id"])
    other = new_book(client, title="別本")
    excluded = []
    real = books_mod.pick_character_variation

    def spy(rng, exclude=None):
        excluded.append(exclude)
        return real(rng, exclude)

    monkeypatch.setattr(books_mod, "pick_character_variation", spy)
    candidates(client, other["id"])
    candidates(client, b["id"])
    assert excluded == [None, last]


def test_character_variation_excludes_latest_any_status(client, app, monkeypatch):
    # 「上一張」＝這本書最近一筆有變化語的角色候選（失敗、捨棄也算）；沒有變化語的舊列略過
    from app.api import books as books_mod

    b = new_book(client)
    gid = candidates(client, b["id"]).json()[0]["id"]
    with app.state.sessions() as s:
        g = s.get(Generation, gid)
        g.status, g.discarded = GenStatus.FAILED, True
        v = json.loads(g.request_json)["variation"]
        s.add(Generation(book_id=b["id"], kind=GenKind.CHARACTER, user_text="x", prompt="x",
                         negative_prompt="old", seed=1, width=8, height=8,
                         request_json=encode_request(kind=GenKind.CHARACTER, prompt="x",
                                                     negative_prompt="old", seed=1, width=8, height=8)))
        s.commit()
    excluded = []
    real = books_mod.pick_character_variation

    def spy(rng, exclude=None):
        excluded.append(exclude)
        return real(rng, exclude)

    monkeypatch.setattr(books_mod, "pick_character_variation", spy)
    candidates(client, b["id"])
    assert excluded == [v]


def test_candidates_validate_prompt_length(client, app):
    b = new_book(client)
    r = client.post(f"/api/books/{b['id']}/character/candidates", json={"description": "熊" * 801})
    assert r.status_code == 422
    with app.state.sessions() as s:
        assert s.exec(select(Generation)).all() == []
    r = client.post(f"/api/books/{b['id']}/character/candidates", json={"description": "熊" * 800})
    assert r.status_code == 202
    assert len(_row(app, r.json()[0]["id"]).prompt) < 2000
    assert r.json()[0]["user_text"] == "熊" * 800


def test_set_character_and_lock(client, app):
    b = new_book(client)
    gens = [{"id": i} for i in candidate_ids(client, b["id"], 2, seed=1)]
    run_jobs(client, app)
    done = client.post(f"/api/books/{b['id']}/character", json={"generation_id": gens[0]["id"]})
    assert done.status_code == 200
    out = done.json()
    assert out["character_generation_id"] == gens[0]["id"]
    assert out["character_description"] == "一隻戴帽子的小熊"
    assert out["updated_at"] >= b["updated_at"]
    assert client.get(f"/api/books/{b['id']}").json()["character_generation_id"] == gens[0]["id"]
    r = client.post(f"/api/books/{b['id']}/character", json={"generation_id": gens[0]["id"]})
    assert r.status_code == 409
    assert candidates(client, b["id"]).status_code == 409


def test_set_character_rejects_invalid(client, app):
    b = new_book(client)
    other = new_book(client, title="別本")
    mine = [{"id": i} for i in candidate_ids(client, b["id"], 3)]
    theirs = candidates(client, other["id"]).json()
    run_jobs(client, app, 4)
    with app.state.sessions() as s:
        s.get(Generation, mine[0]["id"]).status = GenStatus.FAILED
        s.get(Generation, mine[1]["id"]).discarded = True
        page = Generation(book_id=b["id"], kind=GenKind.PAGE, status=GenStatus.SUCCEEDED,
                          user_text="x", prompt="x", negative_prompt="", seed=1, width=8,
                          height=8, request_json="{}")
        s.add(page)
        s.commit()
        page_id = page.id
    for gid in (mine[0]["id"], mine[1]["id"], theirs[0]["id"], page_id):
        r = client.post(f"/api/books/{b['id']}/character", json={"generation_id": gid})
        assert r.status_code == 409, gid
        assert r.json()["detail"]
    assert client.post(f"/api/books/{b['id']}/character", json={"generation_id": 9999}).status_code == 404
    assert client.get(f"/api/books/{b['id']}").json()["character_generation_id"] is None


def test_set_character_is_atomic_under_race(client, app, monkeypatch):
    from app.api import books as books_mod

    b = new_book(client)
    gens = [{"id": i} for i in candidate_ids(client, b["id"], 2, seed=1)]
    run_jobs(client, app, 2)
    real = books_mod.owned_book  # 端點裡的載入（路由層級的檢查用 deps 的，不受影響）

    def racing(session, owner, book_id):
        book = real(session, owner, book_id)  # 驗證時角色尚未設定
        with app.state.sessions() as other:  # 驗證後、寫入前，另一請求搶先設定
            from app.models import Book
            row = other.get(Book, book_id)
            row.character_generation_id = gens[1]["id"]
            row.character_description = "先到者"
            other.add(row)
            other.commit()
        return book

    monkeypatch.setattr(books_mod, "owned_book", racing)
    r = client.post(f"/api/books/{b['id']}/character", json={"generation_id": gens[0]["id"]})
    assert r.status_code == 409
    monkeypatch.undo()
    got = client.get(f"/api/books/{b['id']}").json()
    assert got["character_generation_id"] == gens[1]["id"]
    assert got["character_description"] == "先到者"


def test_candidates_blocked_by_racing_set_character(client, app, monkeypatch):
    from app.api import books as books_mod
    from app.models import Book

    b = new_book(client)
    real = books_mod.owned_book  # 端點裡的載入（路由層級的檢查用 deps 的，不受影響）

    def racing(session, owner, book_id):
        book = real(session, owner, book_id)
        with app.state.sessions() as other:
            row = other.get(Book, book_id)
            row.character_generation_id = 1
            other.add(row)
            other.commit()
        return book

    monkeypatch.setattr(books_mod, "owned_book", racing)
    assert candidates(client, b["id"]).status_code == 409
    with app.state.sessions() as s:
        assert s.exec(select(Generation)).all() == []


def test_candidates_seed_range(client, app):
    b = new_book(client)
    top = 2**32 - 1
    r = candidates(client, b["id"], seed=top)
    assert r.status_code == 202 and _row(app, r.json()[0]["id"]).seed == top
    assert candidates(client, b["id"], seed=top + 1).status_code == 422
    assert candidates(client, b["id"], seed=-1).status_code == 422


def test_candidates_notify_worker(client, app, monkeypatch):
    calls = []
    monkeypatch.setattr(app.state.worker, "notify", lambda: calls.append(1))
    candidates(client, new_book(client)["id"])
    assert calls == [1]


def test_blank_text_rejected_and_stripped(client, app):
    bad = client.post("/api/books", json={"title": "   ", "paper_size": "a4", "print_mode": "print_shop"})
    assert bad.status_code == 422
    b = new_book(client, title="  小熊  ")
    assert b["title"] == "小熊"
    assert client.patch(f"/api/books/{b['id']}", json={"title": "  "}).status_code == 422
    assert client.patch(f"/api/books/{b['id']}", json={"title": " 新名 "}).json()["title"] == "新名"
    assert candidates(client, b["id"], description="   ").status_code == 422
    g = candidates(client, b["id"], description="  小熊  ", seed=1).json()[0]
    with app.state.sessions() as s:
        assert s.get(Generation, g["id"]).user_text == "小熊"


def test_set_character_blocked_by_racing_discard(client, app, monkeypatch):
    from app.api import books as books_mod

    b = new_book(client)
    gens = [{"id": i} for i in candidate_ids(client, b["id"], 2, seed=1)]
    run_jobs(client, app, 2)
    real = books_mod.utcnow

    def racing():
        with app.state.sessions() as other:  # 驗證後、UPDATE 前，另一請求捨棄該候選圖
            row = other.get(Generation, gens[0]["id"])
            row.discarded = True
            other.add(row)
            other.commit()
        return real()

    monkeypatch.setattr(books_mod, "utcnow", racing)
    r = client.post(f"/api/books/{b['id']}/character", json={"generation_id": gens[0]["id"]})
    assert r.status_code == 409
    monkeypatch.undo()
    assert client.get(f"/api/books/{b['id']}").json()["character_generation_id"] is None


def test_set_character_cancels_other_queued_candidates(client, app):
    b = new_book(client)
    gens = [{"id": i} for i in candidate_ids(client, b["id"], 4, seed=1)]
    run_jobs(client, app, 1)
    r = client.post(f"/api/books/{b['id']}/character", json={"generation_id": gens[0]["id"]})
    assert r.status_code == 200
    statuses = [client.get(f"/api/generations/{g['id']}").json()["status"] for g in gens]
    assert statuses == ["succeeded", "cancelled", "cancelled", "cancelled"]
    assert not client.portal.call(app.state.worker.run_once)
    with app.state.sessions() as s:
        assert s.get(Generation, gens[1]["id"]).finished_at is not None


def test_change_size_same_group_only(client):
    b = new_book(client, paper_size="a4", print_mode="home")
    url = f"/api/books/{b['id']}"
    r = client.patch(url, json={"paper_size": "a5"})
    assert r.status_code == 200 and r.json()["paper_size"] == "a5"
    assert r.json()["title"] == b["title"]
    r = client.patch(url, json={"paper_size": "letter"})
    assert r.status_code == 422
    assert "只能改成同比例的紙張尺寸（A4、A5、B5）" in r.json()["detail"]
    assert client.get(url).json()["paper_size"] == "a5"
    r = client.patch(url, json={"print_mode": "print_shop"})
    assert r.status_code == 200 and r.json()["print_mode"] == "print_shop"
    assert client.patch(url, json={}).status_code == 200
    assert client.patch(url, json={"paper_size": "A0"}).status_code == 422
    assert client.patch(url, json={"title": "  "}).status_code == 422


def test_book_stats_and_cover(client, app):
    from tests.test_api_lineart import make_passed, selected_page, set_page
    from tests.test_api_pages import new_page

    b = new_book(client)
    assert b["stats"] == {"pages": 0, "passed": 0,
                          "ready": 0, "attention": 0, "working": 0, "todo": 0}
    assert b["cover_url"] is None

    b, p1, _ = selected_page(client, app, gap=0)
    p0 = new_page(client, b["id"])  # 新頁、沒選圖
    p2 = new_page(client, b["id"])
    # p1 通過；p2 選圖但 QA 有缺口（直接寫入 qa_json）
    make_passed(app, p1["id"])
    got = client.get(f"/api/books/{b['id']}").json()
    assert got["stats"] == {"pages": 3, "passed": 1,
                            "ready": 1, "attention": 0, "working": 0, "todo": 2}
    # 封面＝position 最小且有選圖的頁；網址與 PageOut.lineart_url 一致
    assert got["cover_url"] == p1["lineart_url"]
    assert got["cover_url"].startswith(f"/api/pages/{p1['id']}/lineart?v=")

    import json as _json

    set_page(app, p1["id"], qa_json=_json.dumps({"errors": ["gaps"]}))
    got = client.get(f"/api/books/{b['id']}").json()
    assert "needs_fix" not in got["stats"] and "no_selection" not in got["stats"]
    listed = {x["id"]: x for x in client.get("/api/books").json()}
    assert listed[b["id"]]["stats"] == got["stats"]
    assert listed[b["id"]]["cover_url"] == got["cover_url"]
    assert p0["position"] == 1 and p2["position"] == 2


def _clear_image(app, gen_id):
    """storage_gc 清掉過期捨棄圖之後的樣子：成功、沒有圖。"""
    with app.state.sessions() as s:
        row = s.get(Generation, gen_id)
        row.image_path = None
        s.add(row)
        s.commit()


def test_set_character_rejects_cleared_image(client, app):
    # 被 storage_gc 清掉圖的候選圖不能變成角色：之後每個角色動作任務都會拿不到角色圖
    b = new_book(client)
    gid = candidate_ids(client, b["id"], 1, seed=1)[0]
    run_jobs(client, app)
    _clear_image(app, gid)
    r = client.post(f"/api/books/{b['id']}/character", json={"generation_id": gid})
    assert r.status_code == 409
    assert client.get(f"/api/books/{b['id']}").json()["character_generation_id"] is None


def test_set_character_blocked_by_racing_gc_clear(client, app, monkeypatch):
    from app.api import books as books_mod

    b = new_book(client)
    gid = candidate_ids(client, b["id"], 1, seed=1)[0]
    run_jobs(client, app)
    real = books_mod.utcnow

    def racing():
        _clear_image(app, gid)  # 驗證後、UPDATE 前，storage_gc 清掉了圖
        return real()

    monkeypatch.setattr(books_mod, "utcnow", racing)
    r = client.post(f"/api/books/{b['id']}/character", json={"generation_id": gid})
    assert r.status_code == 409
    monkeypatch.undo()
    assert client.get(f"/api/books/{b['id']}").json()["character_generation_id"] is None


def _two_done_candidates(client, app, b):
    ids = candidate_ids(client, b["id"], 2, seed=1)
    run_jobs(client, app, 2)
    return ids


def test_delete_candidate_discards_it(client, app):
    b = new_book(client)
    g1, g2 = _two_done_candidates(client, app, b)
    assert client.post(f"/api/books/{b['id']}/character", json={"generation_id": g1}).status_code == 200
    r = client.delete(f"/api/books/{b['id']}/character/candidates/{g2}")
    assert r.status_code == 204
    row = _row(app, g2)
    assert row.discarded is True and row.discarded_at is not None
    first_at = row.discarded_at
    listed = [g["id"] for g in client.get(f"/api/books/{b['id']}/generations?kind=character").json()]
    assert g2 not in listed and g1 in listed
    # 重複刪除：冪等，discarded_at 不往後延
    assert client.delete(f"/api/books/{b['id']}/character/candidates/{g2}").status_code == 204
    assert _row(app, g2).discarded_at == first_at
    # 復原
    back = client.patch(f"/api/generations/{g2}", json={"discarded": False})
    assert back.status_code == 200 and back.json()["discarded"] is False
    listed = [g["id"] for g in client.get(f"/api/books/{b['id']}/generations?kind=character").json()]
    assert g2 in listed


def test_delete_chosen_candidate_is_409(client, app):
    b = new_book(client)
    g1, _g2 = _two_done_candidates(client, app, b)
    client.post(f"/api/books/{b['id']}/character", json={"generation_id": g1})
    r = client.delete(f"/api/books/{b['id']}/character/candidates/{g1}")
    assert r.status_code == 409 and r.json()["detail"] == "選好的主角不能刪"
    assert _row(app, g1).discarded is False


def test_delete_active_candidate_is_409(client, app):
    b = new_book(client)
    (g1,) = candidate_ids(client, b["id"], 1, seed=1)  # QUEUED
    r = client.delete(f"/api/books/{b['id']}/character/candidates/{g1}")
    assert r.status_code == 409 and r.json()["detail"] == "這張候選還在畫，請先取消"
    assert _row(app, g1).discarded is False


def test_delete_candidate_wrong_kind_or_book_is_404(client, app):
    b = new_book(client)
    other = new_book(client)
    (g1,) = candidate_ids(client, b["id"], 1, seed=1)
    (o1,) = candidate_ids(client, other["id"], 1, seed=1)
    run_jobs(client, app, 2)
    with app.state.sessions() as s:
        gen = s.get(Generation, g1)
        pose = Generation(**{**gen.model_dump(exclude={"id"}), "kind": GenKind.POSE})
        s.add(pose)
        s.commit()
        pose_id = pose.id
    assert client.delete(f"/api/books/{b['id']}/character/candidates/987654").status_code == 404
    for gid in (pose_id, o1):
        r = client.delete(f"/api/books/{b['id']}/character/candidates/{gid}")
        assert r.status_code == 404 and r.json()["detail"] == "找不到這張候選圖", gid
    assert _row(app, o1).discarded is False and _row(app, pose_id).discarded is False


def test_book_stats_count_review_pose_as_ready(client, app):
    """首頁書卡的「N / M 頁可以印」：review_pose 是軟標記，算可以印、不算請看一下。"""
    from tests.test_api_lineart import make_passed, selected_page, set_page
    from tests.test_api_pages import new_page

    b, p1, _ = selected_page(client, app, gap=0)
    p2 = new_page(client, b["id"])
    make_passed(app, p1["id"], attention="review_pose", attention_detail="請看一下：換了新的主角動作")
    make_passed(app, p2["id"], attention="failed", attention_detail="圖片產生失敗")
    stats = client.get(f"/api/books/{b['id']}").json()["stats"]
    assert {k: stats[k] for k in ("pages", "passed", "ready", "attention")} == \
        {"pages": 2, "passed": 2, "ready": 1, "attention": 1}
    listed = {x["id"]: x for x in client.get("/api/books").json()}
    assert listed[b["id"]]["stats"] == stats
