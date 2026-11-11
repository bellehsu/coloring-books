"""分享完成的繪本（spec §4.9，Phase 10 Task 10）。"""

import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlmodel import select

from app.config import load_settings
from app.main import create_app
from app.models import Owner, PageStatus, Usage, utcnow
from app.services import auth
from tests.fake_engine import FakeEngine
from tests.test_api_export import npages
from tests.test_api_lineart import A5_HOME, color_png, make_passed, page_out, seed_generation, set_page
from tests.test_api_pages import book_with_character, new_page

COOKIE = "cb_session"
GONE = "找不到這本分享的繪本"


@pytest.fixture
def dev_owner():
    return None  # 沒有 cookie 就是 401


@pytest.fixture
def world(settings_file):
    app = create_app(load_settings(settings_file), engine=FakeEngine(), start_worker=False,
                     startup_preflight=False)
    with TestClient(app) as client:
        sids, ids = {}, {}
        with app.state.sessions() as s:
            tokens = {}
            for who, name, admin in (("a", "小明", False), ("b", "小華", False), ("admin", "管理者", True)):
                owner, tok = auth.create_owner(s, name, is_admin=admin)
                tokens[who], ids[who] = tok, owner.id
        for who, tok in tokens.items():
            client.cookies.clear()
            assert client.get(f"/i/{tok}", follow_redirects=False).status_code == 303
            sids[who] = client.cookies[COOKIE]
        yield app, client, sids, ids


def as_(client, sid):
    client.cookies.clear()
    if sid is not None:
        client.cookies.set(COOKIE, sid)


def a_book(client, app, sid, n=3, passed=(0,)):
    """目前身分建一本有 n 頁（皆有選定圖）的書，passed 是通過的頁序號。回傳 (book, pages)。"""
    as_(client, sid)
    app.state.engine.image = color_png()
    b, _ = book_with_character(client, app, **A5_HOME)
    pages = []
    for _ in range(n):
        p = new_page(client, b["id"])
        set_page(app, p["id"], selected_generation_id=seed_generation(app, b["id"], p["id"], color_png()))
        pages.append(page_out(client, b["id"], p["id"]))
    for i in passed:
        make_passed(app, pages[i]["id"])
    return b, pages


def share(client, book_id, shared=True):
    return client.put(f"/api/books/{book_id}/share", json={"shared": shared})


def test_toggle_rules(world):
    app, client, sids, ids = world
    b, pages = a_book(client, app, sids["a"], passed=())
    r = share(client, b["id"])
    assert r.status_code == 409 and r.json()["detail"] == "至少完成一頁才能分享"
    assert client.get(f"/api/books/{b['id']}").json()["shared_at"] is None
    make_passed(app, pages[0]["id"])
    r = share(client, b["id"])
    assert r.status_code == 200 and r.json()["shared_at"] is not None
    assert share(client, b["id"], False).json()["shared_at"] is None
    # 別人的書：404；admin：可以
    as_(client, sids["b"])
    assert share(client, b["id"]).status_code == 404
    as_(client, sids["admin"])
    assert share(client, b["id"]).status_code == 200


def test_list_visibility(world):
    app, client, sids, ids = world
    shared, sp = a_book(client, app, sids["a"], n=3, passed=(0, 2))
    assert share(client, shared["id"]).status_code == 200
    unshared, _ = a_book(client, app, sids["a"], passed=(0,))
    zero, zp = a_book(client, app, sids["a"], passed=(0,))
    assert share(client, zero["id"]).status_code == 200
    set_page(app, zp[0]["id"], status=PageStatus.DRAFT)  # 之後不再有完成的頁
    mine_b, _ = a_book(client, app, sids["b"], passed=(0,))
    assert share(client, mine_b["id"]).status_code == 200
    gone, _ = a_book(client, app, sids["admin"], passed=(0,))
    assert share(client, gone["id"]).status_code == 200
    with app.state.sessions() as s:
        admin = s.get(Owner, ids["admin"])
        admin.revoked_at = utcnow()
        s.add(admin)
        s.commit()
    as_(client, sids["b"])
    items = client.get("/api/shared/books").json()
    assert [i["id"] for i in items] == [shared["id"]]
    it = items[0]
    assert it["author"] == "小明" and it["page_count"] == 2 and it["mine"] is False
    assert it["title"] == shared["title"] and it["paper_size"] == shared["paper_size"]
    assert it["cover_page_id"] == sp[0]["id"] and it["shared_at"]
    # A 自己看：不含自己的書（首頁用徽章標示），只有 B 的
    as_(client, sids["a"])
    assert [i["id"] for i in client.get("/api/shared/books").json()] == [mine_b["id"]]


def test_list_newest_first(world):
    app, client, sids, ids = world
    first, _ = a_book(client, app, sids["a"])
    second, _ = a_book(client, app, sids["a"])
    share(client, first["id"])
    share(client, second["id"])
    as_(client, sids["b"])
    assert [i["id"] for i in client.get("/api/shared/books").json()] == [second["id"], first["id"]]


def test_detail_image_pdf(world):
    app, client, sids, ids = world
    b, pages = a_book(client, app, sids["a"], n=3, passed=(0, 2))
    share(client, b["id"])
    set_page(app, pages[2]["id"], position=0)
    set_page(app, pages[0]["id"], position=1)
    as_(client, sids["b"])
    d = client.get(f"/api/shared/books/{b['id']}").json()
    assert d["author"] == "小明" and d["mine"] is False
    assert d["pages"] == [{"id": pages[2]["id"], "position": 0}, {"id": pages[0]["id"], "position": 1}]
    for p in d["pages"]:
        r = client.get(f"/api/shared/pages/{p['id']}/image.png")
        assert r.status_code == 200 and r.headers["content-type"] == "image/png"
        assert r.headers["cache-control"] == "private, max-age=300"
        assert max(Image.open(io.BytesIO(r.content)).size) <= 1600
    assert client.get(f"/api/shared/pages/{pages[1]['id']}/image.png").status_code == 404  # 未完成
    r = client.get(f"/api/shared/books/{b['id']}/export.pdf")
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
    assert r.headers["content-type"] == "application/pdf" and npages(r.content) == 2
    # 擁有者自己看：mine
    as_(client, sids["a"])
    assert client.get(f"/api/shared/books/{b['id']}").json()["mine"] is True


def test_unshare_and_revoke_take_effect_immediately(world):
    app, client, sids, ids = world
    b, pages = a_book(client, app, sids["a"])
    share(client, b["id"])
    pid = pages[0]["id"]
    urls = [f"/api/shared/books/{b['id']}", f"/api/shared/pages/{pid}/image.png",
            f"/api/shared/books/{b['id']}/export.pdf"]
    as_(client, sids["b"])
    assert [client.get(u).status_code for u in urls] == [200, 200, 200]
    as_(client, sids["a"])
    share(client, b["id"], False)
    as_(client, sids["b"])
    for u in urls:
        r = client.get(u)
        assert r.status_code == 404 and r.json()["detail"] == GONE, u
    as_(client, sids["a"])
    share(client, b["id"])
    with app.state.sessions() as s:
        a = s.get(Owner, ids["a"])
        a.revoked_at = utcnow()
        s.add(a)
        s.commit()
    as_(client, sids["b"])
    for u in urls:
        assert client.get(u).status_code == 404, u
    for u in ("/api/shared/books/999", "/api/shared/pages/999/image.png", "/api/shared/books/999/export.pdf"):
        r = client.get(u)
        assert r.status_code == 404 and r.json()["detail"] == GONE


def test_requires_session(world):
    app, client, sids, ids = world
    b, pages = a_book(client, app, sids["a"])
    share(client, b["id"])
    as_(client, None)
    for u in ("/api/shared/books", f"/api/shared/books/{b['id']}",
              f"/api/shared/pages/{pages[0]['id']}/image.png", f"/api/shared/books/{b['id']}/export.pdf"):
        assert client.get(u).status_code == 401, u
    assert client.put(f"/api/books/{b['id']}/share", json={"shared": False}).status_code == 401


def test_viewing_does_not_count_usage(world):
    app, client, sids, ids = world
    b, pages = a_book(client, app, sids["a"])
    share(client, b["id"])

    def usage():
        with app.state.sessions() as s:
            return sorted((u.owner_id, u.day, u.generations) for u in s.exec(select(Usage)).all())

    before = usage()
    as_(client, sids["b"])
    client.get("/api/shared/books")
    client.get(f"/api/shared/books/{b['id']}")
    client.get(f"/api/shared/pages/{pages[0]['id']}/image.png")
    assert client.get(f"/api/shared/books/{b['id']}/export.pdf").status_code == 200
    assert usage() == before


def test_shared_routes_do_not_open_owner_routes(world):
    app, client, sids, ids = world
    b, pages = a_book(client, app, sids["a"])
    share(client, b["id"])
    as_(client, sids["b"])
    for u in (f"/api/books/{b['id']}", f"/api/books/{b['id']}/pages", f"/api/books/{b['id']}/export.pdf",
              f"/api/pages/{pages[0]['id']}/print.png"):
        assert client.get(u).status_code == 404, u


def test_shared_image_of_unshared_book_page_is_404(world):
    """擁有者有兩本書：只分享其中一本。另一本（沒分享）已完成的頁面，不能經 /api/shared/pages/{id}/image.png 取得。"""
    app, client, sids, ids = world
    shared, shared_pages = a_book(client, app, sids["a"])
    other, other_pages = a_book(client, app, sids["a"])  # 同一個擁有者的另一本，不分享
    share(client, shared["id"])
    as_(client, sids["b"])
    assert client.get(f"/api/shared/pages/{shared_pages[0]['id']}/image.png").status_code == 200
    r = client.get(f"/api/shared/pages/{other_pages[0]['id']}/image.png")
    assert r.status_code == 404 and r.json()["detail"] == GONE
