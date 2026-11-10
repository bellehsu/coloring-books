import io
import json

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from app.config import load_settings
from app.main import create_app
from app.models import Generation, GenKind, GenStatus, Page
from tests.fake_engine import FakeEngine, make_png
from tests.test_api_books import candidates, new_book, run_jobs


@pytest.fixture
def app(settings_file):
    return create_app(load_settings(settings_file), engine=FakeEngine(), start_worker=False)


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


def new_page(client, book_id, **kw):
    r = client.post(f"/api/books/{book_id}/pages", json=kw)
    assert r.status_code == 201, r.text
    return r.json()


def book_with_character(client, app, **kw):
    b = new_book(client, **kw)
    gens = candidates(client, b["id"], seed=1, count=1).json()
    run_jobs(client, app)
    r = client.post(f"/api/books/{b['id']}/character", json={"generation_id": gens[0]["id"]})
    assert r.status_code == 200
    return b, gens[0]["id"]


def sketch_files(app):
    return list(app.state.storage.root.rglob("sketch*.png"))


def upload(client, page_id, data, name="s.png"):
    return client.put(f"/api/pages/{page_id}/sketch", files={"file": (name, data, "image/png")})


def test_pages_append_and_reorder(client):
    b = new_book(client)
    pages = [new_page(client, b["id"]) for _ in range(3)]
    assert [p["position"] for p in pages] == [0, 1, 2]
    assert client.get("/api/books/999/pages").status_code == 404
    ids = [p["id"] for p in pages]
    r = client.post(f"/api/books/{b['id']}/pages/order", json={"page_ids": ids[::-1]})
    assert r.status_code == 200
    got = client.get(f"/api/books/{b['id']}/pages").json()
    assert [p["id"] for p in got] == ids[::-1]
    assert [p["position"] for p in got] == [0, 1, 2]
    other = new_page(client, new_book(client)["id"])
    for bad in (ids[:2], ids + [ids[0]], ids[:2] + [other["id"]], ids + [other["id"]]):
        r = client.post(f"/api/books/{b['id']}/pages/order", json={"page_ids": bad})
        assert r.status_code == 400, bad
    assert [p["id"] for p in client.get(f"/api/books/{b['id']}/pages").json()] == ids[::-1]
    assert new_page(client, b["id"])["position"] == 3


def test_controlnet_strength_range(client):
    p = new_page(client, new_book(client)["id"])
    assert p["controlnet_strength"] == 0.4 and p["sketch_url"] is None
    assert client.patch(f"/api/pages/{p['id']}", json={"controlnet_strength": 0.7}).status_code == 422
    r = client.patch(f"/api/pages/{p['id']}", json={"controlnet_strength": 0.6})
    assert r.status_code == 200 and r.json()["controlnet_strength"] == 0.6
    r = client.patch(f"/api/pages/{p['id']}", json={"scene_prompt": "公園"})
    assert r.json()["scene_prompt"] == "公園" and r.json()["controlnet_strength"] == 0.6
    assert client.patch("/api/pages/999", json={}).status_code == 404


def test_scene_prompt_length(client, app):
    b, _ = book_with_character(client, app)
    r = client.post(f"/api/books/{b['id']}/pages", json={"scene_prompt": "景" * 801})
    assert r.status_code == 422
    p = new_page(client, b["id"], scene_prompt="景" * 800)
    assert client.patch(f"/api/pages/{p['id']}", json={"scene_prompt": "景" * 801}).status_code == 422
    with app.state.sessions() as s:  # 角色描述也塞滿 800 字
        from app.models import Book
        bk = s.get(Book, b["id"])
        bk.character_description = "熊" * 800
        s.add(bk)
        s.commit()
    r = client.post(f"/api/pages/{p['id']}/candidates", json={"count": 1})
    assert r.status_code == 202
    assert len(r.json()[0]["prompt"]) < 2000


def test_sketch_upload_rejects_bad_files(client, app):
    p = new_page(client, new_book(client)["id"])
    r = upload(client, p["id"], b"hello, not an image", "a.txt")
    assert r.status_code == 400 and "圖片" in r.json()["detail"]
    r = upload(client, p["id"], b"\0" * (10_000_000 + 1))
    assert r.status_code == 400
    r = upload(client, p["id"], make_png((32, 32)))
    assert r.status_code == 400
    assert sketch_files(app) == []
    with app.state.sessions() as s:
        assert s.get(Page, p["id"]).sketch_path is None
    r = upload(client, p["id"], make_png((128, 128)))
    assert r.status_code == 200 and r.json()["sketch_url"]
    assert client.get(r.json()["sketch_url"]).headers["content-type"] == "image/png"
    assert len(sketch_files(app)) == 1
    # 之後被拒絕的上傳不動既有草圖
    assert upload(client, p["id"], b"junk").status_code == 400
    assert len(sketch_files(app)) == 1


def test_sketch_delete_and_get(client, app):
    p = new_page(client, new_book(client)["id"])
    assert client.get(f"/api/pages/{p['id']}/sketch").status_code == 404
    upload(client, p["id"], make_png((128, 128)))
    r = client.delete(f"/api/pages/{p['id']}/sketch")
    assert r.status_code == 200 and r.json()["sketch_url"] is None
    assert sketch_files(app) == []
    assert client.get(f"/api/pages/{p['id']}/sketch").status_code == 404
    assert client.delete("/api/pages/999/sketch").status_code == 404
    assert upload(client, 999, make_png((128, 128))).status_code == 404


def test_page_candidates_require_character(client):
    p = new_page(client, new_book(client)["id"])
    r = client.post(f"/api/pages/{p['id']}/candidates", json={})
    assert r.status_code == 409 and "角色" in r.json()["detail"]
    assert client.post("/api/pages/999/candidates", json={}).status_code == 404


def test_page_candidates_snapshot_sketch(client, app):
    b, char_gen = book_with_character(client, app)
    p = new_page(client, b["id"], scene_prompt="在公園玩")
    upload(client, p["id"], make_png((128, 128)))
    r = client.post(f"/api/pages/{p['id']}/candidates", json={"count": 2, "seed": 5})
    assert r.status_code == 202
    gens = r.json()
    assert [g["seed"] for g in gens] == [5, 6]
    assert all(g["kind"] == "page" and g["page_id"] == p["id"] for g in gens)
    layout = b["layout"]["generation_size"]
    with app.state.sessions() as s:
        char_path = s.get(Generation, char_gen).image_path
        rows = [s.get(Generation, g["id"]) for g in gens]
    snaps = []
    for g in rows:
        req = json.loads(g.request_json)
        assert req["structure_image"] == f"books/{b['id']}/pages/{p['id']}/sketch_{g.id}.png"
        assert app.state.storage.abs(req["structure_image"]).is_file()
        assert req["structure_strength"] == 0.4
        assert req["character_image"] == char_path
        assert [req["width"], req["height"]] == layout == [g.width, g.height]
        assert g.user_text == "在公園玩" and "在公園玩" in g.prompt
        snaps.append(req["structure_image"])
    assert snaps[0] != snaps[1]
    client.delete(f"/api/pages/{p['id']}/sketch")
    assert all(app.state.storage.abs(x).is_file() for x in snaps)
    # 沒有草圖：structure_image 為 None
    r = client.post(f"/api/pages/{p['id']}/candidates", json={"count": 1})
    with app.state.sessions() as s:
        req = json.loads(s.get(Generation, r.json()[0]["id"]).request_json)
    assert req["structure_image"] is None


def test_page_candidates_seed_range_and_notify(client, app, monkeypatch):
    b, _ = book_with_character(client, app)
    p = new_page(client, b["id"])
    top = 2**32 - 1
    r = client.post(f"/api/pages/{p['id']}/candidates", json={"seed": top - 2, "count": 4})
    assert r.status_code == 422 and "seed" in r.json()["detail"]
    calls = []
    monkeypatch.setattr(app.state.worker, "notify", lambda: calls.append(1))
    assert client.post(f"/api/pages/{p['id']}/candidates", json={"seed": top - 3, "count": 4}).status_code == 202
    assert calls == [1]


def test_page_candidates_rollback_removes_snapshots(client, app, monkeypatch):
    b, _ = book_with_character(client, app)
    p = new_page(client, b["id"])
    upload(client, p["id"], make_png((128, 128)))
    from app.api import pages as pages_mod

    calls = {"n": 0}
    real = pages_mod.encode_request

    def flaky(**kw):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("boom")
        return real(**kw)

    monkeypatch.setattr(pages_mod, "encode_request", flaky)
    with TestClient(app, raise_server_exceptions=False) as c2:
        r = c2.post(f"/api/pages/{p['id']}/candidates", json={"count": 3})
    assert r.status_code == 500
    assert [f.name for f in sketch_files(app)] == ["sketch.png"]
    with app.state.sessions() as s:
        assert s.exec(select(Generation).where(Generation.kind == GenKind.PAGE)).all() == []


def test_select_rejects_invalid_generation(client, app):
    b, char_gen = book_with_character(client, app)
    p, q = new_page(client, b["id"]), new_page(client, b["id"])
    mine = client.post(f"/api/pages/{p['id']}/candidates", json={"count": 4, "seed": 1}).json()
    theirs = client.post(f"/api/pages/{q['id']}/candidates", json={"count": 1}).json()
    run_jobs(client, app, 5)
    with app.state.sessions() as s:
        s.get(Generation, mine[0]["id"]).status = GenStatus.FAILED
        s.get(Generation, mine[1]["id"]).discarded = True
        s.commit()
    url = f"/api/pages/{p['id']}/select"
    for gid in (mine[0]["id"], mine[1]["id"], theirs[0]["id"], char_gen):
        r = client.post(url, json={"generation_id": gid})
        assert r.status_code == 409, gid
        assert r.json()["detail"]
    assert client.post(url, json={"generation_id": 9999}).status_code == 404
    assert client.post("/api/pages/999/select", json={"generation_id": 1}).status_code == 404
    r = client.post(url, json={"generation_id": mine[2]["id"]})
    assert r.status_code == 200 and r.json()["selected_generation_id"] == mine[2]["id"]
    client.post(f"/api/pages/{p['id']}/candidates", json={"count": 1})
    got = client.get(f"/api/books/{b['id']}/pages").json()
    assert got[0]["selected_generation_id"] == mine[2]["id"]


def test_select_blocked_by_racing_discard(client, app):
    from sqlalchemy import event

    b, _ = book_with_character(client, app)
    p = new_page(client, b["id"])
    g = client.post(f"/api/pages/{p['id']}/candidates", json={"count": 1}).json()[0]
    run_jobs(client, app)
    fired = []

    def discard_before_update(conn, cursor, statement, params, context, executemany):
        if statement.startswith("UPDATE page") and not fired:
            fired.append(1)  # 驗證通過後、寫入前，另一請求捨棄了這筆
            with app.state.sessions() as other:
                row = other.get(Generation, g["id"])
                row.discarded = True
                other.add(row)
                other.commit()

    event.listen(app.state.db, "before_cursor_execute", discard_before_update)
    try:
        r = client.post(f"/api/pages/{p['id']}/select", json={"generation_id": g["id"]})
    finally:
        event.remove(app.state.db, "before_cursor_execute", discard_before_update)
    assert fired and r.status_code == 409
    assert client.get(f"/api/books/{b['id']}/pages").json()[0]["selected_generation_id"] is None


def test_page_candidates_fail_when_sketch_file_missing(client, app):
    b, _ = book_with_character(client, app)
    p = new_page(client, b["id"])
    upload(client, p["id"], make_png((128, 128)))
    for f in sketch_files(app):
        f.unlink()
    r = client.post(f"/api/pages/{p['id']}/candidates", json={"count": 2})
    assert r.status_code == 409 and r.json()["detail"] == "草圖檔案遺失，請重新上傳"
    with app.state.sessions() as s:
        assert s.exec(select(Generation).where(Generation.kind == GenKind.PAGE)).all() == []
    assert sketch_files(app) == []


def test_delete_page_cascades_and_reorders(client, app):
    from app.models import LineartEdit
    from tests.test_api_lineart import color_png

    app.state.engine.image = color_png()
    b, _ = book_with_character(client, app)
    pages = [new_page(client, b["id"]) for _ in range(3)]
    mid = pages[1]
    done = client.post(f"/api/pages/{mid['id']}/candidates", json={"count": 1}).json()
    run_jobs(client, app, 1)
    queued = client.post(f"/api/pages/{mid['id']}/candidates", json={"count": 2}).json()
    assert client.post(f"/api/pages/{mid['id']}/select", json={"generation_id": done[0]["id"]}).status_code == 200
    assert client.get(f"/api/pages/{mid['id']}/lineart").status_code == 200  # 產生線稿檔
    assert upload(client, mid["id"], color_png(), "s.png").status_code == 200
    storage = app.state.storage
    img = storage.abs(f"books/{b['id']}/generations/{done[0]['id']}.png")
    pdir = storage.abs(f"books/{b['id']}/pages/{mid['id']}")
    assert img.is_file() and pdir.is_dir()

    assert client.delete(f"/api/pages/{mid['id']}").status_code == 204
    assert client.delete(f"/api/pages/{mid['id']}").status_code == 404
    got = client.get(f"/api/books/{b['id']}/pages").json()
    assert [p["id"] for p in got] == [pages[0]["id"], pages[2]["id"]]
    assert [p["position"] for p in got] == [0, 1]
    assert not img.exists() and not pdir.exists()
    with app.state.sessions() as s:
        assert s.exec(select(Generation).where(Generation.page_id == mid["id"])).all() == []
        assert s.exec(select(LineartEdit).where(LineartEdit.page_id == mid["id"])).all() == []
        # 被取消而非執行：已不存在，worker 也沒東西可跑
        assert all(s.get(Generation, g["id"]) is None for g in queued)
    assert client.portal.call(app.state.worker.run_once) is False


def test_delete_page_cancels_queued_generations(client, app):
    b, _ = book_with_character(client, app)
    p = new_page(client, b["id"])
    q = client.post(f"/api/pages/{p['id']}/candidates", json={"count": 2}).json()
    assert client.delete(f"/api/pages/{p['id']}").status_code == 204
    assert client.portal.call(app.state.worker.run_once) is False
    assert q


def test_delete_page_with_running_generation_409(client, app):
    b, _ = book_with_character(client, app)
    p = new_page(client, b["id"])
    gens = client.post(f"/api/pages/{p['id']}/candidates", json={"count": 2}).json()
    with app.state.sessions() as s:
        g = s.get(Generation, gens[0]["id"])
        g.status = GenStatus.RUNNING
        s.add(g)
        s.commit()
    r = client.delete(f"/api/pages/{p['id']}")
    assert r.status_code == 409
    assert r.json()["detail"] == "這一頁還有圖片正在生成，請稍後再刪"
    with app.state.sessions() as s:  # 整個交易回復：頁面與排隊中的都還在
        assert s.get(Page, p["id"]) is not None
        assert s.get(Generation, gens[1]["id"]).status == GenStatus.QUEUED


def test_deleted_page_and_generation_ids_are_never_reused(client, app):
    """刪掉 id 最大的頁面後再新增，新頁面（與其生成紀錄）不可沿用舊 id，前端以 id 為 key 的狀態才不會串頁。"""
    b, _ = book_with_character(client, app)
    a = new_page(client, b["id"])
    victim = new_page(client, b["id"])
    old_gens = client.post(f"/api/pages/{victim['id']}/candidates", json={"count": 2}).json()
    assert client.delete(f"/api/pages/{victim['id']}").status_code == 204
    c = new_page(client, b["id"])
    assert c["id"] != victim["id"]
    assert c["id"] > victim["id"] > a["id"]
    new_gens = client.post(f"/api/pages/{c['id']}/candidates", json={"count": 2}).json()
    assert not {g["id"] for g in new_gens} & {g["id"] for g in old_gens}
    assert min(g["id"] for g in new_gens) > max(g["id"] for g in old_gens)


def test_patch_action_prompt(client):
    p = new_page(client, new_book(client)["id"])
    assert p["action_prompt"] == ""
    r = client.patch(f"/api/pages/{p['id']}", json={"action_prompt": "抱著一本書往上爬"})
    assert r.status_code == 200 and r.json()["action_prompt"] == "抱著一本書往上爬"
    got = client.get(f"/api/books/{p['book_id']}/pages").json()
    assert got[0]["action_prompt"] == "抱著一本書往上爬"
    assert client.patch(f"/api/pages/{p['id']}", json={"action_prompt": "動" * 301}).status_code == 422


def test_create_page_with_texts(client):
    b = new_book(client)
    r = client.post(f"/api/books/{b['id']}/pages", json={"scene_prompt": "森林", "action_prompt": "揮手"})
    assert r.status_code == 201
    assert r.json()["scene_prompt"] == "森林" and r.json()["action_prompt"] == "揮手"
