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


def test_controlnet_strength_is_ignored(client):
    """構圖強度已停用（0006 移除欄位）：舊前端送的值照收但忽略；PageOut 不再有草圖欄位。"""
    p = new_page(client, new_book(client)["id"])
    assert "controlnet_strength" not in p and "sketch_url" not in p
    for v in (0.6, 0.0, 0.7):
        r = client.patch(f"/api/pages/{p['id']}", json={"controlnet_strength": v})
        assert r.status_code == 200 and "controlnet_strength" not in r.json()
    r = client.patch(f"/api/pages/{p['id']}", json={"scene_prompt": "公園", "controlnet_strength": 0.2})
    assert r.status_code == 200 and r.json()["scene_prompt"] == "公園"
    assert client.patch("/api/pages/999", json={}).status_code == 404


def test_scene_prompt_length(client, app):
    b, _ = book_with_character(client, app)
    r = client.post(f"/api/books/{b['id']}/pages", json={"scene_prompt": "景" * 801})
    assert r.status_code == 422
    p = new_page(client, b["id"], scene_prompt="景" * 800)
    assert client.patch(f"/api/pages/{p['id']}", json={"scene_prompt": "景" * 801}).status_code == 422
    r = client.post(f"/api/pages/{p['id']}/backgrounds")  # 800 字的場景描述照樣能產生背景
    assert r.status_code == 202, r.text
    with app.state.sessions() as s:
        assert len(s.get(Generation, r.json()["id"]).prompt) < 2000


def queue_backgrounds(client, page_id, n):
    """替頁面排 n 張背景（不執行）。頁面要有場景描述。"""
    out = []
    for _ in range(n):
        r = client.post(f"/api/pages/{page_id}/backgrounds")
        assert r.status_code == 202, r.text
        out.append(r.json())
    return out


def test_delete_page_cascades_and_reorders(client, app):
    from app.models import LineartEdit, PageHistory, PageTask
    from tests.test_api_lineart import color_png, seed_generation, set_page

    app.state.engine.image = color_png()
    b, _ = book_with_character(client, app)
    pages = [new_page(client, b["id"], scene_prompt="公園") for _ in range(3)]
    mid = pages[1]
    done = seed_generation(app, b["id"], mid["id"], color_png())
    queued = queue_backgrounds(client, mid["id"], 2)
    set_page(app, mid["id"], selected_generation_id=done)
    assert client.get(f"/api/pages/{mid['id']}/lineart").status_code == 200  # 產生線稿檔
    storage = app.state.storage
    img = f"books/{b['id']}/generations/{done}.png"
    pdir = f"books/{b['id']}/pages/{mid['id']}/"
    assert storage.exists(img) and list(storage.list(pdir))
    with app.state.sessions() as s:  # 頁面的工作與復原紀錄（Phase 13）也跟著刪
        s.add(PageTask(page_id=mid["id"]))
        s.add(PageHistory(page_id=mid["id"], seq=1, snapshot_json="{}"))
        s.add(PageHistory(page_id=pages[0]["id"], seq=1, snapshot_json="{}"))
        s.commit()

    assert client.delete(f"/api/pages/{mid['id']}").status_code == 204
    assert client.delete(f"/api/pages/{mid['id']}").status_code == 404
    got = client.get(f"/api/books/{b['id']}/pages").json()
    assert [p["id"] for p in got] == [pages[0]["id"], pages[2]["id"]]
    assert [p["position"] for p in got] == [0, 1]
    assert not storage.exists(img) and list(storage.list(pdir)) == []
    with app.state.sessions() as s:
        assert s.exec(select(Generation).where(Generation.page_id == mid["id"])).all() == []
        assert s.exec(select(LineartEdit).where(LineartEdit.page_id == mid["id"])).all() == []
        assert s.exec(select(PageTask)).all() == []
        assert [h.page_id for h in s.exec(select(PageHistory)).all()] == [pages[0]["id"]]
        # 被取消而非執行：已不存在，worker 也沒東西可跑
        assert all(s.get(Generation, g["id"]) is None for g in queued)
    assert client.portal.call(app.state.worker.run_once) is False


def test_delete_page_cancels_queued_generations(client, app):
    b, _ = book_with_character(client, app)
    p = new_page(client, b["id"], scene_prompt="公園")
    q = queue_backgrounds(client, p["id"], 2)
    assert client.delete(f"/api/pages/{p['id']}").status_code == 204
    assert client.portal.call(app.state.worker.run_once) is False
    assert q


def test_delete_page_with_running_generation_409(client, app):
    b, _ = book_with_character(client, app)
    p = new_page(client, b["id"], scene_prompt="公園")
    gens = queue_backgrounds(client, p["id"], 2)
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
    victim = new_page(client, b["id"], scene_prompt="公園")
    old_gens = queue_backgrounds(client, victim["id"], 2)
    assert client.delete(f"/api/pages/{victim['id']}").status_code == 204
    c = new_page(client, b["id"], scene_prompt="公園")
    assert c["id"] != victim["id"]
    assert c["id"] > victim["id"] > a["id"]
    new_gens = queue_backgrounds(client, c["id"], 2)
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


# ---- Phase 9：構圖草圖與舊整頁流程的端點已移除 ------------------------------------------


def test_sketch_endpoints_removed(client):
    p = new_page(client, new_book(client)["id"])
    url = f"/api/pages/{p['id']}/sketch"
    assert client.put(url, files={"file": ("s.png", make_png((128, 128)), "image/png")}).status_code in (404, 405)
    assert client.delete(url).status_code in (404, 405)
    assert client.get(url).status_code in (404, 405)
    r = client.post(f"/api/books/{p['book_id']}/reference-sketch",
                    files={"file": ("s.png", make_png((128, 128)), "image/png")})
    assert r.status_code in (404, 405)


def test_legacy_creation_removed(client, app):
    b, _ = book_with_character(client, app)
    p = new_page(client, b["id"], scene_prompt="公園")
    assert client.post(f"/api/pages/{p['id']}/candidates", json={"count": 1}).status_code in (404, 405)
    assert client.post(f"/api/pages/{p['id']}/select", json={"generation_id": 1}).status_code in (404, 405)
    with app.state.sessions() as s:
        assert s.exec(select(Generation).where(Generation.kind == GenKind.PAGE)).all() == []


def test_legacy_page_still_exports(client, app):
    """舊流程頁面（選定圖是 PAGE）照樣有線稿、可通過、可匯出單頁與整本 PDF。"""
    from tests.test_api_lineart import legacy_page, make_passed

    b, p, ids = legacy_page(client, app)
    with app.state.sessions() as s:
        assert s.get(Generation, ids[0]).kind == GenKind.PAGE
    assert p["selected_generation_id"] == ids[0] and p["lineart_url"]
    assert client.get(f"/api/pages/{p['id']}/lineart").status_code == 200
    make_passed(app, p["id"])
    r = client.get(f"/api/pages/{p['id']}/print.png")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    r = client.get(f"/api/books/{b['id']}/export.pdf")
    assert r.status_code == 200 and r.content.startswith(b"%PDF")


def test_legacy_page_redo_replaces_page_image(client, app):
    """舊流程頁面用新流程重做：選背景與角色動作時舊整頁圖仍是選定圖，合成後換成新的合成圖。"""
    from tests.test_api_compose import confirm, gen_background, gen_pose, picked
    from tests.test_api_lineart import legacy_page

    b, p, ids = legacy_page(client, app, scene_prompt="公園")
    pid = p["id"]
    picked(client, pid, "background", gen_background(client, app, pid))
    page = picked(client, pid, "pose", gen_pose(client, app, pid))
    assert page["selected_generation_id"] == ids[0]
    page = confirm(client, pid)
    gid = page["selected_generation_id"]
    assert gid != ids[0]
    with app.state.sessions() as s:
        assert s.get(Generation, gid).kind == GenKind.COMPOSITE
        assert s.get(Generation, ids[0]) is not None  # 舊整頁圖留在歷史紀錄
    assert client.get(f"/api/pages/{pid}/lineart").status_code == 200


def test_caption_patch_trim_clear_and_limits(client):
    p = new_page(client, new_book(client)["id"])
    assert p["caption_zh"] is None and p["caption_en"] is None
    url = f"/api/pages/{p['id']}"
    r = client.patch(url, json={"caption_zh": "  消防車 ", "caption_en": " Fire Engine"})
    assert r.status_code == 200
    assert (r.json()["caption_zh"], r.json()["caption_en"]) == ("消防車", "Fire Engine")
    # 沒送的欄位不動
    r = client.patch(url, json={"scene_prompt": "x"})
    assert (r.json()["caption_zh"], r.json()["caption_en"]) == ("消防車", "Fire Engine")
    r = client.patch(url, json={"caption_zh": ""})
    assert r.json()["caption_zh"] is None and r.json()["caption_en"] == "Fire Engine"
    r = client.patch(url, json={"caption_en": "   "})
    assert r.json()["caption_en"] is None
    client.patch(url, json={"caption_zh": "a", "caption_en": "b"})
    r = client.patch(url, json={"caption_zh": None, "caption_en": None})
    assert (r.json()["caption_zh"], r.json()["caption_en"]) == (None, None)
    assert client.get(f"/api/books/{p['book_id']}/pages").json()[0]["caption_zh"] is None


def test_caption_limits_count_code_points(client):
    p = new_page(client, new_book(client)["id"])
    url = f"/api/pages/{p['id']}"
    r = client.patch(url, json={"caption_zh": "字" * 13})
    assert r.status_code == 422 and r.json()["detail"][0]["msg"] == "中文字最多 12 個字"
    ok = "𠮷" + "字" * 11  # 12 個 code point（UTF-16 是 13）
    r = client.patch(url, json={"caption_zh": ok})
    assert r.status_code == 200 and r.json()["caption_zh"] == ok
    assert client.patch(url, json={"caption_zh": "𠮷" * 13}).status_code == 422
    r = client.patch(url, json={"caption_en": "a" * 29})
    assert r.status_code == 422 and r.json()["detail"][0]["msg"] == "英文字最多 28 個字元"
    assert client.patch(url, json={"caption_en": "a" * 28}).status_code == 200
    assert client.patch(url, json={"caption_en": "𠮷" * 28}).status_code == 200
    assert client.patch(url, json={"caption_en": "𠮷" * 29}).status_code == 422
    # 去頭尾空白後才計算
    assert client.patch(url, json={"caption_zh": " " + "字" * 12 + " "}).status_code == 200


def test_caption_change_keeps_passed_status_and_selection(client, app):
    from app.models import PageStatus
    from tests.test_api_lineart import get_page, make_passed, selected_page

    _, p, ids = selected_page(client, app)
    make_passed(app, p["id"])
    r = client.patch(f"/api/pages/{p['id']}", json={"caption_zh": "消防車", "caption_en": "Fire Engine"})
    assert r.status_code == 200 and r.json()["status"] == "passed"
    page = get_page(app, p["id"])
    assert page.status == PageStatus.PASSED and page.passed_at is not None
    assert page.selected_generation_id == ids[0]


def test_patch_cleanup_fields(client):
    p = new_page(client, new_book(client)["id"], scene_prompt="海邊")
    assert p["auto_cleanup"] is False and p["cleanup_prompt"] is None
    r = client.patch(f"/api/pages/{p['id']}", json={"auto_cleanup": True, "cleanup_prompt": "  清掉沙灘上的垃圾  "})
    assert r.status_code == 200 and r.json()["auto_cleanup"] is True
    assert r.json()["cleanup_prompt"] == "清掉沙灘上的垃圾"
    r = client.patch(f"/api/pages/{p['id']}", json={"cleanup_prompt": "   "})
    assert r.json()["cleanup_prompt"] is None and r.json()["auto_cleanup"] is True
    assert client.patch(f"/api/pages/{p['id']}", json={"cleanup_prompt": "清" * 600}).status_code == 200
    assert client.patch(f"/api/pages/{p['id']}", json={"cleanup_prompt": "清" * 601}).status_code == 422
    assert client.patch(f"/api/pages/{p['id']}", json={"auto_cleanup": "yes"}).status_code == 422
    assert client.patch(f"/api/pages/{p['id']}", json={"auto_cleanup": None}).status_code == 422


def _cleanup_page(client):
    p = new_page(client, new_book(client)["id"], scene_prompt="海邊")
    r = client.patch(f"/api/pages/{p['id']}", json={"auto_cleanup": True, "cleanup_prompt": "清掉垃圾"})
    assert r.status_code == 200
    return p["id"]


def test_patch_scene_change_turns_cleanup_off(client):
    pid = _cleanup_page(client)
    r = client.patch(f"/api/pages/{pid}", json={"scene_prompt": "公園", "action_prompt": "跑步"})
    assert r.status_code == 200
    assert r.json()["auto_cleanup"] is False and r.json()["cleanup_prompt"] is None


def test_patch_same_scene_keeps_cleanup(client):
    pid = _cleanup_page(client)
    r = client.patch(f"/api/pages/{pid}", json={"scene_prompt": "  海邊 ", "action_prompt": "跑步"})
    assert r.status_code == 200
    assert r.json()["auto_cleanup"] is True and r.json()["cleanup_prompt"] == "清掉垃圾"


def test_patch_scene_with_explicit_cleanup_keeps_explicit(client):
    pid = _cleanup_page(client)
    r = client.patch(f"/api/pages/{pid}", json={"scene_prompt": "公園", "auto_cleanup": True})
    assert r.json()["auto_cleanup"] is True and r.json()["cleanup_prompt"] is None  # 沒送的 cleanup_prompt 照規則清掉
    r = client.patch(f"/api/pages/{pid}", json={"scene_prompt": "森林", "auto_cleanup": True, "cleanup_prompt": "清掉樹枝"})
    assert r.json()["auto_cleanup"] is True and r.json()["cleanup_prompt"] == "清掉樹枝"
    r = client.patch(f"/api/pages/{pid}", json={"scene_prompt": "山", "cleanup_prompt": "清掉石頭"})
    assert r.json()["auto_cleanup"] is False and r.json()["cleanup_prompt"] == "清掉石頭"
