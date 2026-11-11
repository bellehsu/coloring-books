"""邀請連結、session cookie、dev_owner 與系統端點的權限（Phase 10 Task 5）。"""

import hashlib
import json
from datetime import timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from app import main as app_main
from app.config import load_settings
from app.main import create_app
from app.models import Book, Generation, GenKind, GenStatus, Owner, OwnerSession, utcnow
from app.services import auth
from tests.fake_engine import FakeEngine
from tests.test_api_lineart import selected_page

COOKIE = "cb_session"
NINETY_DAYS = 90 * 24 * 3600


@pytest.fixture
def dev_owner():
    return None  # 這個模組測真正的邀請連結：沒有 cookie 就是 401


def make_app(settings_file, **auth_kw):
    s = load_settings(settings_file)
    if auth_kw:
        s = s.model_copy(update={"auth": s.auth.model_copy(update=auth_kw)})
    return create_app(s, engine=FakeEngine(), start_worker=False)


@pytest.fixture
def app(settings_file):
    return make_app(settings_file)


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


def new_owner(app, name="小明", **kw) -> tuple[Owner, str]:
    with app.state.sessions() as s:
        owner, token = auth.create_owner(s, name, **kw)
        s.expunge(owner)
        return owner, token


def login(client, token) -> str:
    """GET /i/{token}，回傳 cookie 裡的 session id；client 之後就以這個 owner 身分送請求。"""
    client.cookies.clear()
    r = client.get(f"/i/{token}", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/", r.text
    return client.cookies[COOKIE]


def revoke(app, owner_id):
    with app.state.sessions() as s:
        o = s.get(Owner, owner_id)
        o.revoked_at = utcnow()
        s.add(o)
        s.commit()


def db_bytes(app) -> bytes:
    p = app.state.settings.paths.database
    data = p.read_bytes()
    wal = p.with_name(p.name + "-wal")
    return data + (wal.read_bytes() if wal.exists() else b"")


def test_hash_token_is_sha256_hex():
    assert auth.hash_token("abc") == hashlib.sha256(b"abc").hexdigest()


def test_invite_link_sets_cookie_and_redirects(client, app):
    owner, token = new_owner(app, cap=12)
    assert owner.daily_generation_cap == 12 and not owner.is_admin
    r = client.get(f"/i/{token}", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"
    set_cookie = r.headers["set-cookie"]
    sid = client.cookies[COOKIE]
    assert set_cookie.startswith(f"{COOKIE}={sid};")
    flags = [f.strip().lower() for f in set_cookie.split(";")[1:]]
    assert "httponly" in flags and "samesite=lax" in flags and "path=/" in flags
    assert f"max-age={NINETY_DAYS}" in flags
    assert "secure" not in flags  # auth.cookie_secure 預設關（本機 http）
    with app.state.sessions() as s:
        rows = s.exec(select(OwnerSession)).all()
        assert [(x.owner_id, x.session_hash) for x in rows] == [(owner.id, auth.hash_token(sid))]
        assert s.get(Owner, owner.id).token_hash == auth.hash_token(token)
    raw = db_bytes(app)
    assert sid.encode() not in raw and token.encode() not in raw  # DB 只存 hash
    assert client.get("/api/books").status_code == 200


def test_cookie_secure_flag(settings_file):
    app = make_app(settings_file, cookie_secure=True)
    with TestClient(app) as client:
        _, token = new_owner(app)
        r = client.get(f"/i/{token}", follow_redirects=False)
    assert "secure" in [f.strip().lower() for f in r.headers["set-cookie"].split(";")]


def test_invalid_or_revoked_link_redirects_to_invalid_page(client, app):
    owner, token = new_owner(app)
    revoke(app, owner.id)
    for t in ("nope", token):
        r = client.get(f"/i/{t}", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/invalid-link", t
        assert "set-cookie" not in r.headers
    with app.state.sessions() as s:
        assert s.exec(select(OwnerSession)).all() == []


def test_no_or_bad_cookie_is_401(client):
    r = client.get("/api/books")
    assert r.status_code == 401 and r.json()["detail"] == "請用邀請連結開啟"
    client.cookies.set(COOKIE, "forged")
    r = client.get("/api/books")
    assert r.status_code == 401 and r.json()["detail"] == "請用邀請連結開啟"


def test_session_expires_after_90_days(client, app):
    _, token = new_owner(app)
    login(client, token)
    assert client.get("/api/books").status_code == 200
    with app.state.sessions() as s:
        row = s.exec(select(OwnerSession)).one()
        row.created_at = utcnow() - timedelta(days=91)
        s.add(row)
        s.commit()
    r = client.get("/api/books")
    assert r.status_code == 401 and r.json()["detail"] == "請用邀請連結開啟"


def test_revoked_owner_gets_401_next_request(client, app):
    owner, token = new_owner(app)
    login(client, token)
    b, p, ids = selected_page(client, app)
    image_urls = [
        f"/api/generations/{ids[0]}/image",
        f"/api/pages/{p['id']}/lineart",
        f"/api/generations/{ids[0]}/lineart-preview",
    ]
    for url in image_urls:
        assert client.get(url, follow_redirects=False).status_code == 302, url
    revoke(app, owner.id)
    for url in ["/api/books", f"/api/books/{b['id']}", f"/api/pages/{p['id']}/qa/overlay.png",
                "/api/system/queue", *image_urls]:
        r = client.get(url, follow_redirects=False)
        assert r.status_code == 401 and r.json()["detail"] == "連結已失效", url
        assert "location" not in r.headers
    r = client.post("/api/books", json={"title": "x", "paper_size": "a4", "print_mode": "home"})
    assert r.status_code == 401
    # 舊連結也不能再登入
    assert client.get(f"/i/{token}", follow_redirects=False).headers["location"] == "/invalid-link"


def test_dev_owner_fallback(settings_file):
    app = make_app(settings_file, dev_owner="admin")
    with TestClient(app) as client:
        assert client.get("/api/books").status_code == 200
        r = client.post("/api/books", json={"title": "x", "paper_size": "a4", "print_mode": "home"})
        assert r.status_code == 201
        assert client.get("/api/books").status_code == 200
        with app.state.sessions() as s:
            owners = s.exec(select(Owner)).all()
            assert [(o.name, o.is_admin) for o in owners] == [("admin", True)]
            assert s.get(Book, r.json()["id"]).owner_id == owners[0].id
        # 壞掉的 cookie 不會退回 dev_owner
        client.cookies.set(COOKIE, "forged")
        assert client.get("/api/books").status_code == 401
        # 有效的 cookie 優先於 dev_owner
        other, token = new_owner(app, "小華")
        login(client, token)
        assert client.get("/api/books").json() == []


def test_dev_owner_reuses_existing_admin(settings_file):
    """遷移或舊版留下的 admin（沒人知道 token）要沿用，不可再建一個。"""
    app = make_app(settings_file, dev_owner="belle")
    with TestClient(app) as client:
        with app.state.sessions() as s:
            s.add(Owner(name="admin", token_hash="x" * 64, is_admin=True))
            s.add(Owner(name="belle", token_hash="y" * 64, is_admin=False))  # 同名但不是 admin：不沿用
            s.commit()
        for _ in range(2):
            assert client.get("/api/books").status_code == 200
        with app.state.sessions() as s:
            assert len(s.exec(select(Owner)).all()) == 2
            admin = s.exec(select(Owner).where(Owner.name == "admin")).one()
        r = client.post("/api/books", json={"title": "x", "paper_size": "a4", "print_mode": "home"})
        with app.state.sessions() as s:
            assert s.get(Book, r.json()["id"]).owner_id == admin.id


def test_dev_owner_prefers_admin_with_that_name(settings_file):
    app = make_app(settings_file, dev_owner="belle")
    with TestClient(app):
        with app.state.sessions() as s:
            s.add(Owner(name="admin", token_hash="x" * 64, is_admin=True))
            s.add(Owner(name="belle", token_hash="y" * 64, is_admin=True))
            s.commit()
            assert auth.resolve_dev_owner(s, "belle").name == "belle"


def test_dev_owner_insert_race_reuses_winner(settings_file, monkeypatch):
    """兩個請求同時建立 dev owner：輸的那個撞到唯一鍵，回頭拿贏家建好的那一列。"""
    app = make_app(settings_file, dev_owner="admin")
    with TestClient(app):
        with app.state.sessions() as s1:
            winner = auth.resolve_dev_owner(s1, "admin")
        real = auth._find_dev_owner
        calls = []

        def stale_first(session, name):
            calls.append(name)
            return None if len(calls) == 1 else real(session, name)  # 第一次查詢時贏家還沒寫入

        monkeypatch.setattr(auth, "_find_dev_owner", stale_first)
        with app.state.sessions() as s2:
            assert auth.resolve_dev_owner(s2, "admin").id == winner.id
        with app.state.sessions() as s:
            assert len(s.exec(select(Owner)).all()) == 1


def test_book_create_sets_owner_and_list_is_filtered(client, app):
    a, ta = new_owner(app, "甲")
    b, tb = new_owner(app, "乙")
    adm, tadm = new_owner(app, "管理者", is_admin=True)
    login(client, ta)
    ba = client.post("/api/books", json={"title": "甲的書", "paper_size": "a4", "print_mode": "home"}).json()
    login(client, tb)
    bb = client.post("/api/books", json={"title": "乙的書", "paper_size": "a4", "print_mode": "home"}).json()
    assert [x["id"] for x in client.get("/api/books").json()] == [bb["id"]]
    login(client, ta)
    assert [x["id"] for x in client.get("/api/books").json()] == [ba["id"]]
    login(client, tadm)
    assert {x["id"] for x in client.get("/api/books").json()} == {ba["id"], bb["id"]}
    with app.state.sessions() as s:
        assert s.get(Book, ba["id"]).owner_id == a.id and s.get(Book, bb["id"]).owner_id == b.id


def test_preflight_admin_only(client, app, monkeypatch):
    from app.services.comfy_client import ComfyClient

    def boom(request):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(app_main, "ComfyClient",
                        lambda url, **kw: ComfyClient(url, transport=httpx.MockTransport(boom), **kw))
    _, token = new_owner(app)
    _, admin_token = new_owner(app, "管理者", is_admin=True)
    assert client.get("/api/system/preflight").status_code == 401
    login(client, token)
    r = client.get("/api/system/preflight")
    assert r.status_code == 403
    login(client, admin_token)
    r = client.get("/api/system/preflight")
    assert r.status_code == 200 and r.json()["comfy_reachable"] is False


def test_queue_has_no_content(client, app):
    a, ta = new_owner(app, "甲")
    _, tb = new_owner(app, "乙")
    _, tadm = new_owner(app, "管理者", is_admin=True)
    secret = "秘密的小熊提示詞"
    login(client, ta)
    book = client.post("/api/books", json={"title": secret, "paper_size": "a4", "print_mode": "home"}).json()
    with app.state.sessions() as s:
        for status in (GenStatus.QUEUED, GenStatus.RUNNING, GenStatus.FAILED):
            s.add(Generation(book_id=book["id"], kind=GenKind.CHARACTER, status=status,
                             user_text=secret, prompt=secret, negative_prompt="", seed=0,
                             width=8, height=8, request_json=json.dumps({"prompt": secret}),
                             error=secret if status == GenStatus.FAILED else None))
        s.commit()
    app.state.worker.last_error = secret  # 例如某人的任務失敗訊息
    login(client, tb)
    r = client.get("/api/system/queue")
    assert r.status_code == 200
    body = r.json()
    assert secret not in r.text
    assert set(body) <= {"state", "last_error", "queued", "running_id", "pending", "recent"}
    assert body["queued"] == 1 and body["running_id"] is None and body["last_error"] is None
    assert body["pending"] == [{"kind": "character"}, {"kind": "character"}]
    for job in body["recent"]:
        assert set(job) == {"kind", "elapsed_s"}
    # 引擎無法連線的訊息是系統狀態（不含任何人的內容），照常顯示
    from app.services.worker import WorkerState

    app.state.worker.state = WorkerState.ENGINE_UNAVAILABLE
    app.state.worker.last_error = "ComfyUI 無法連線"
    assert client.get("/api/system/queue").json()["last_error"] == "ComfyUI 無法連線"
    # admin 看得到全部
    app.state.worker.state = WorkerState.IDLE
    app.state.worker.last_error = secret
    login(client, tadm)
    body = client.get("/api/system/queue").json()
    assert body["last_error"] == secret and body["running_id"] is not None
