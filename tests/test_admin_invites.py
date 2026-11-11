"""管理者的邀請管理 API（spec §4.10，Phase 10 Task 11）：GET/POST /api/admin/invites、GET /api/me。"""

import importlib.util
import os
import sqlite3
import stat
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import load_settings
from app.main import create_app
from app.models import Book, Owner, Usage
from app.services import auth
from app.services.usage import clock, taipei_day
from tests.fake_engine import FakeEngine

COOKIE = "cb_session"
DUP = "這個名字已經有邀請了"


@pytest.fixture
def dev_owner():
    return None


def make_app(settings_file, *, auth_kw=None, env_key=None, monkeypatch=None):
    if env_key is not None:
        monkeypatch.setenv("APP_AUTH__LINK_KEY", env_key)
    s = load_settings(settings_file)
    if auth_kw:
        s = s.model_copy(update={"auth": s.auth.model_copy(update=auth_kw)})
    return create_app(s, engine=FakeEngine(), start_worker=False, startup_preflight=False)


@pytest.fixture
def app(settings_file):
    return make_app(settings_file)


@pytest.fixture
def world(app):
    """app 與 client；admin 與一般使用者都已登入過（sids），client 目前沒有 cookie。"""
    with TestClient(app) as client:
        sids = {}
        with app.state.sessions() as s:
            toks = {"admin": auth.create_owner(s, "管理者", is_admin=True)[1],
                    "user": auth.create_owner(s, "小明")[1]}
        for who, tok in toks.items():
            client.cookies.clear()
            assert client.get(f"/i/{tok}", follow_redirects=False).status_code == 303
            sids[who] = client.cookies[COOKIE]
        client.cookies.clear()
        yield app, client, sids


def as_(client, sid):
    client.cookies.clear()
    if sid is not None:
        client.cookies.set(COOKIE, sid)


def create(client, **body):
    return client.post("/api/admin/invites", json={"name": "小美", **body})


# --- 存取 ---


def test_non_admin_gets_404_and_nothing_is_written(world):
    app, client, sids = world
    as_(client, sids["user"])
    assert client.get("/api/admin/invites").status_code == 404
    r = create(client)
    assert r.status_code == 404
    with app.state.sessions() as s:
        assert s.query(Owner).count() == 2


def test_no_session_is_401(world):
    _, client, _ = world
    assert client.get("/api/admin/invites").status_code == 401
    assert create(client).status_code == 401
    assert client.get("/api/me").status_code == 401


def test_me(world):
    _, client, sids = world
    as_(client, sids["admin"])
    assert client.get("/api/me").json() == {"name": "管理者", "is_admin": True}
    as_(client, sids["user"])
    assert client.get("/api/me").json() == {"name": "小明", "is_admin": False}


# --- 建立與列表 ---


def test_create_returns_link_that_logs_in_as_new_owner(world):
    app, client, sids = world
    as_(client, sids["admin"])
    r = create(client, daily_generation_cap=25)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"] == "小美" and body["is_admin"] is False and body["daily_generation_cap"] == 25
    assert body["today"] == 0 and body["book_count"] == 0 and body["last_seen"] is None
    assert body["revoked"] is False and body["created_at"]
    link = body["link"]
    assert "/i/" in link and link.startswith("http://testserver/i/")
    token = link.rsplit("/i/", 1)[1]
    with TestClient(app) as fresh:  # 沒有任何 cookie 的另一個瀏覽器
        r = fresh.get(f"/i/{token}", follow_redirects=False)
        assert r.status_code == 303 and COOKIE in fresh.cookies
        assert fresh.get("/api/books").status_code == 200
        assert fresh.get("/api/me").json() == {"name": "小美", "is_admin": False}
        assert fresh.get("/api/admin/invites").status_code == 404


def test_list_newest_first_and_shows_same_link_again(world):
    _, client, sids = world
    as_(client, sids["admin"])
    first = create(client, name="甲").json()
    second = create(client, name="乙", is_admin=True).json()
    rows = client.get("/api/admin/invites").json()
    assert [r["name"] for r in rows] == ["乙", "甲", "小明", "管理者"]
    by_name = {r["name"]: r for r in rows}
    assert by_name["甲"]["link"] == first["link"] and by_name["乙"]["link"] == second["link"]
    assert by_name["乙"]["is_admin"] is True and by_name["乙"]["daily_generation_cap"] is None  # admin 不設上限
    assert by_name["小明"]["link"] is None  # 用 create_owner 直接建立、沒有金鑰：不能再顯示


def test_duplicate_active_name_is_409_but_revoked_name_can_be_reused(world):
    app, client, sids = world
    as_(client, sids["admin"])
    assert create(client).status_code == 201
    r = create(client, name=" 小美 ")  # 前後空白不算不同名字
    assert r.status_code == 409 and r.json()["detail"] == DUP
    with app.state.sessions() as s:
        o = s.query(Owner).filter(Owner.name == "小美").one()
        o.revoked_at = auth.utcnow()
        s.add(o)
        s.commit()
    assert create(client).status_code == 201


@pytest.mark.parametrize("body", [
    {"name": ""}, {"name": "   "}, {"name": "x" * 41},
    {"daily_generation_cap": 0}, {"daily_generation_cap": 1001}, {"daily_generation_cap": -5},
])
def test_invalid_input_is_422(world, body):
    app, client, sids = world
    as_(client, sids["admin"])
    r = client.post("/api/admin/invites", json={"name": "小美", **body})
    assert r.status_code == 422, r.text
    with app.state.sessions() as s:
        assert s.query(Owner).count() == 2


def test_name_boundaries_are_accepted(world):
    _, client, sids = world
    as_(client, sids["admin"])
    assert create(client, name="x" * 40).status_code == 201
    assert create(client, name="a", daily_generation_cap=1).status_code == 201
    assert create(client, name="b", daily_generation_cap=1000).status_code == 201


def test_today_book_count_last_seen_and_revoked(world):
    app, client, sids = world
    as_(client, sids["admin"])
    token = create(client).json()["link"].rsplit("/i/", 1)[1]
    row = next(r for r in client.get("/api/admin/invites").json() if r["name"] == "小美")
    assert row["last_seen"] is None and row["today"] == 0 and row["book_count"] == 0
    with TestClient(app) as other:  # 新 owner 打開連結、發出請求
        other.get(f"/i/{token}", follow_redirects=False)
        assert other.post("/api/books", json={"title": "書", "paper_size": "a5",
                                              "print_mode": "home"}).status_code == 201
    with app.state.sessions() as s:
        oid = s.query(Owner).filter(Owner.name == "小美").one().id
        s.add(Usage(owner_id=oid, day=taipei_day(clock()), generations=7))
        s.add(Usage(owner_id=oid, day=taipei_day(clock()).replace(year=2020), generations=30))
        s.commit()
        assert s.query(Book).filter(Book.owner_id == oid).count() == 1
    row = next(r for r in client.get("/api/admin/invites").json() if r["name"] == "小美")
    assert row["today"] == 7 and row["book_count"] == 1 and row["last_seen"] is not None
    assert row["revoked"] is False
    with app.state.sessions() as s:
        o = s.get(Owner, oid)
        o.revoked_at = auth.utcnow()
        s.add(o)
        s.commit()
    row = next(r for r in client.get("/api/admin/invites").json() if r["name"] == "小美")
    assert row["revoked"] is True and row["link"] is None  # 已撤銷：連結已失效，不再顯示


# --- 儲存的 token ---


def test_db_never_holds_the_raw_token(world):
    app, client, sids = world
    as_(client, sids["admin"])
    link = create(client).json()["link"]
    token = link.rsplit("/i/", 1)[1]
    con = sqlite3.connect(app.state.settings.paths.database)
    try:
        dump = "\n".join(con.iterdump())
    finally:
        con.close()
    assert token not in dump
    with app.state.sessions() as s:
        enc = s.query(Owner).filter(Owner.name == "小美").one().token_encrypted
    assert enc and token not in enc


def test_legacy_row_without_encrypted_token_has_null_link(world):
    app, client, sids = world
    as_(client, sids["admin"])
    row = next(r for r in client.get("/api/admin/invites").json() if r["name"] == "小明")
    assert row["link"] is None  # token_encrypted 是 NULL（Task 11 之前建立的）


def test_wrong_key_gives_null_link_not_500(settings_file, monkeypatch):
    app1 = make_app(settings_file, env_key="first-key", monkeypatch=monkeypatch)
    with TestClient(app1) as c1:
        with app1.state.sessions() as s:
            tok = auth.create_owner(s, "管理者", is_admin=True)[1]
        c1.get(f"/i/{tok}", follow_redirects=False)
        assert create(c1).json()["link"]
    app2 = make_app(settings_file, env_key="another-key", monkeypatch=monkeypatch)
    with TestClient(app2) as c2:
        c2.cookies.set(COOKIE, c1.cookies[COOKIE])
        r = c2.get("/api/admin/invites")
        assert r.status_code == 200
        assert next(x for x in r.json() if x["name"] == "小美")["link"] is None


def test_garbage_ciphertext_gives_null_link(world):
    app, client, sids = world
    as_(client, sids["admin"])
    create(client)
    with app.state.sessions() as s:
        o = s.query(Owner).filter(Owner.name == "小美").one()
        o.token_encrypted = "not-a-fernet-token"
        s.add(o)
        s.commit()
    row = next(r for r in client.get("/api/admin/invites").json() if r["name"] == "小美")
    assert row["link"] is None


# --- 連結前綴 ---


def test_invite_base_url_setting_wins(settings_file):
    app = make_app(settings_file, auth_kw={"invite_base_url": "https://books.example/"})
    with TestClient(app) as client:
        with app.state.sessions() as s:
            tok = auth.create_owner(s, "管理者", is_admin=True)[1]
        client.get(f"/i/{tok}", follow_redirects=False)
        link = client.post("/api/admin/invites", json={"name": "甲"},
                           headers={"X-Forwarded-Host": "evil.test"}).json()["link"]
        assert link.startswith("https://books.example/i/") and "//i/" not in link
        listed = client.get("/api/admin/invites").json()
        assert next(r for r in listed if r["name"] == "甲")["link"] == link


def test_unset_base_url_uses_request_origin_and_forwarded_headers(world):
    _, client, sids = world
    as_(client, sids["admin"])
    plain = create(client, name="甲").json()["link"]
    assert plain.startswith("http://testserver/i/")
    fwd = create(client, name="乙", ).json()
    assert fwd["link"].startswith("http://testserver/i/")
    r = client.post("/api/admin/invites", json={"name": "丙"},
                    headers={"X-Forwarded-Host": "192.168.1.5:3000", "X-Forwarded-Proto": "https"})
    assert r.json()["link"].startswith("https://192.168.1.5:3000/i/")
    r = client.get("/api/admin/invites", headers={"X-Forwarded-Host": "lan.example:3010, proxy.internal"})
    link = next(x for x in r.json() if x["name"] == "甲")["link"]
    assert link.startswith("http://lan.example:3010/i/")  # 只取第一個；沒有 proto 就用請求本身的
    r = client.get("/api/admin/invites", headers={"Host": "10.0.0.7:3000"})
    assert next(x for x in r.json() if x["name"] == "甲")["link"].startswith("http://10.0.0.7:3000/i/")


def test_malformed_forwarded_host_is_ignored(world):
    _, client, sids = world
    as_(client, sids["admin"])
    r = client.post("/api/admin/invites", json={"name": "甲"},
                    headers={"X-Forwarded-Host": "a.test/evil?x=", "X-Forwarded-Proto": "javascript"})
    assert r.json()["link"].startswith("http://testserver/i/")


# --- 金鑰來源 ---


def test_link_key_file_is_created_0600_and_stable_across_restarts(settings_file):
    app1 = make_app(settings_file)
    with TestClient(app1) as c1:
        with app1.state.sessions() as s:
            tok = auth.create_owner(s, "管理者", is_admin=True)[1]
        c1.get(f"/i/{tok}", follow_redirects=False)
        link = create(c1).json()["link"]
        sid = c1.cookies[COOKIE]
    key_file = app1.state.settings.paths.storage_dir / ".link_key"
    assert key_file.exists() and stat.S_IMODE(os.stat(key_file).st_mode) == 0o600
    app2 = make_app(settings_file)  # 重啟：同一個 storage_dir
    with TestClient(app2) as c2:
        c2.cookies.set(COOKIE, sid)
        row = next(r for r in c2.get("/api/admin/invites").json() if r["name"] == "小美")
        assert row["link"] == link


def test_link_key_from_env_overrides_file(settings_file, monkeypatch):
    app = make_app(settings_file, env_key="from-env", monkeypatch=monkeypatch)
    with TestClient(app) as c:
        with app.state.sessions() as s:
            tok = auth.create_owner(s, "管理者", is_admin=True)[1]
        c.get(f"/i/{tok}", follow_redirects=False)
        assert create(c).json()["link"]
    assert not (app.state.settings.paths.storage_dir / ".link_key").exists()
    assert app.state.settings.auth.link_key.get_secret_value() == "from-env"
    assert "from-env" not in repr(app.state.settings)


def test_link_key_in_settings_file_is_rejected(settings_file):
    import yaml

    from app.config import SettingsError

    data = yaml.safe_load(settings_file.read_text(encoding="utf-8"))
    data["auth"]["link_key"] = "oops"
    settings_file.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(SettingsError):
        load_settings(settings_file)


# --- CLI 與 API 共用 ---


def _cli():
    spec = importlib.util.spec_from_file_location("invites_cli", Path(__file__).resolve().parents[1] / "scripts" / "invites.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_cli_create_then_api_list_shows_link(world, settings_file, capsys):
    app, client, sids = world
    cli = _cli()
    assert cli.main(["--settings", str(settings_file), "create", "--name", "阿華",
                     "--base-url", "https://books.example"]) == 0
    printed = [ln for ln in capsys.readouterr().out.splitlines() if "/i/" in ln][0].strip()
    as_(client, sids["admin"])
    row = next(r for r in client.get("/api/admin/invites").json() if r["name"] == "阿華")
    assert row["link"].rsplit("/i/", 1)[1] == printed.rsplit("/i/", 1)[1]  # 同一個 token（前綴用請求的 origin）


def test_cli_rotate_updates_stored_link_and_list_show_links(world, settings_file, capsys):
    app, client, sids = world
    cli = _cli()
    as_(client, sids["admin"])
    created = create(client).json()
    oid = created["id"]
    assert cli.main(["--settings", str(settings_file), "rotate", "--id", str(oid)]) == 0
    new_tok = [ln for ln in capsys.readouterr().out.splitlines() if "/i/" in ln][0].rsplit("/i/", 1)[1].strip()
    row = next(r for r in client.get("/api/admin/invites").json() if r["id"] == oid)
    assert row["link"].rsplit("/i/", 1)[1] == new_tok != created["link"].rsplit("/i/", 1)[1]
    # list 預設不顯示連結；--show-links 才多一欄「連結」
    assert cli.main(["--settings", str(settings_file), "list"]) == 0
    assert new_tok not in capsys.readouterr().out
    assert cli.main(["--settings", str(settings_file), "list", "--show-links"]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0].split()[-1] == "連結" and new_tok in out
