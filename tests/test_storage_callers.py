"""Task 2：呼叫端與引擎只用 bytes（put/get/delete/delete_prefix），不再碰 Path。"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import load_settings
from app.main import create_app
from app.models import Generation, GenKind
from app.services.page_lineart import ensure_page_lineart
from app.storage import LocalStorage, Storage
from tests.fake_engine import FakeEngine
from tests.test_api_books import run_jobs
from tests.test_api_lineart import A5_HOME, color_png, seed_generation, selected_page, set_page
from tests.test_api_pages import book_with_character, new_page

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def app(settings_file):
    return create_app(load_settings(settings_file), engine=FakeEngine(), start_worker=False)


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


class SpyStorage:
    """包住真正的 Storage，記錄每次呼叫的方法名稱與 key。"""

    def __init__(self, inner: Storage):
        self.inner = inner
        self.calls: list[tuple[str, str]] = []

    def __getattr__(self, name):
        attr = getattr(self.inner, name)
        if name in ("put", "get", "delete", "exists", "signed_url", "list", "delete_prefix"):
            def wrapped(key, *a, **kw):
                self.calls.append((name, key))
                return attr(key, *a, **kw)
            return wrapped
        return attr


def test_no_abs_calls():
    hits = [
        f"{p.relative_to(REPO)}:{i}"
        for p in (REPO / "backend" / "app").rglob("*.py")
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if re.search(r"storage\w*\.(abs|write)\(", line)
    ]
    assert hits == []


def test_storage_alias_removed_and_protocol_renamed():
    import app.storage as st

    assert not hasattr(st, "StorageProtocol")
    assert st.Storage is not st.LocalStorage
    for name in ("abs", "write"):
        assert not hasattr(st.LocalStorage, name), name


def test_local_storage_requires_signing_key(tmp_path):
    with pytest.raises(TypeError):
        LocalStorage(tmp_path)  # type: ignore[call-arg]


def test_engine_receives_character_bytes(client, app):
    b, char_gen = book_with_character(client, app)
    p = new_page(client, b["id"], scene_prompt="公園")
    r = client.post(f"/api/pages/{p['id']}/poses", json={"action": "waving hand"})
    assert r.status_code == 202, r.text
    run_jobs(client, app)
    req = app.state.engine.requests[-1]
    assert req.kind == GenKind.POSE
    with app.state.sessions() as s:
        key = s.get(Generation, char_gen).image_path
        rj = json.loads(s.get(Generation, r.json()["id"]).request_json)
    assert isinstance(req.character_image, bytes)
    assert req.character_image == app.state.storage.get(key)
    assert rj["character_image"] == key  # request_json 記 key，不是 bytes


def test_worker_fails_clearly_when_character_object_missing(client, app):
    b, char_gen = book_with_character(client, app)
    p = new_page(client, b["id"], scene_prompt="公園")
    with app.state.sessions() as s:
        app.state.storage.delete(s.get(Generation, char_gen).image_path)
    r = client.post(f"/api/pages/{p['id']}/poses", json={"action": "waving hand"})
    assert r.status_code == 202, r.text
    run_jobs(client, app)
    g = client.get(f"/api/generations/{r.json()['id']}").json()
    assert g["status"] == "failed" and "角色參考圖檔案遺失" in g["error"]


def test_missing_generation_object_lineart_regenerates_or_404(client, app):
    _, p, ids = selected_page(client, app)
    with app.state.sessions() as s:
        app.state.storage.delete(s.get(Generation, ids[0]).image_path)
    for url in (f"/api/pages/{p['id']}/lineart", f"/api/pages/{p['id']}/qa/overlay.png"):
        r = client.get(url)
        assert r.status_code == 404, (url, r.text)
        assert "圖片檔案遺失" in r.json()["detail"]
    r = client.get(f"/api/generations/{ids[0]}/image")
    assert r.status_code == 404 and r.json()["detail"] == "圖片檔案遺失"
    # 角色動作的去背圖：圖或遮罩的物件不見了也是 404「圖片檔案遺失」
    from tests.test_api_compose import compose_ready, gen_row

    _, _, _, pose = compose_ready(client, app)
    app.state.storage.delete(gen_row(app, pose).image_path)
    r = client.get(f"/api/generations/{pose}/cutout")
    assert r.status_code == 404 and r.json()["detail"] == "圖片檔案遺失"


def test_no_abs_pattern_ignores_numpy():
    pattern = re.compile(r"storage\w*\.(abs|write)\(")
    assert not pattern.search("diff = np.abs(arr - bg)")
    assert pattern.search("p = storage.abs(rel)") and pattern.search("self.storage.write(rel, b)")
    assert pattern.search("app.state.storage.abs(x)")


def test_missing_lineart_object_regenerates(client, app):
    _, p, _ = selected_page(client, app)
    first = client.get(f"/api/pages/{p['id']}/lineart")
    assert first.status_code == 200
    with app.state.sessions() as s:
        from app.models import Page

        key = s.get(Page, p["id"]).lineart_path
    app.state.storage.delete(key)
    again = client.get(f"/api/pages/{p['id']}/lineart")
    assert again.status_code == 200 and again.content == first.content
    assert app.state.storage.exists(key)


def test_lineart_existence_uses_db_key(client, app):
    _, p, _ = selected_page(client, app)
    spy = SpyStorage(app.state.storage)
    with app.state.sessions() as s:
        first = ensure_page_lineart(s, spy, p["id"])
    assert ("put", first.path) in spy.calls
    spy.calls.clear()
    with app.state.sessions() as s:
        second = ensure_page_lineart(s, spy, p["id"])
    assert second.path == first.path and second.key == first.key
    assert spy.calls == []  # 同一個 key：只看資料庫，不呼叫 exists／get


def test_missing_preview_object_rerenders(client, app):
    _, _, ids = selected_page(client, app)
    url = f"/api/generations/{ids[0]}/lineart-preview"
    first = client.get(url)
    assert first.status_code == 200
    previews = list(app.state.storage.list(f"cache/previews/{ids[0]}_"))
    assert len(previews) == 1
    app.state.storage.delete(previews[0])
    again = client.get(url)
    assert again.status_code == 200 and again.content == first.content


def test_delete_page_removes_objects(client, app):
    app.state.engine.image = color_png()
    b, char_gen = book_with_character(client, app, **A5_HOME)
    keep = new_page(client, b["id"])
    p = new_page(client, b["id"])
    gens = [seed_generation(app, b["id"], p["id"], color_png()) for _ in range(2)]
    other = seed_generation(app, b["id"], keep["id"], color_png())
    set_page(app, p["id"], selected_generation_id=gens[0])
    assert client.get(f"/api/pages/{p['id']}/lineart").status_code == 200
    assert client.get(f"/api/generations/{gens[1]}/lineart-preview").status_code == 200
    storage = app.state.storage
    with app.state.sessions() as s:
        keys = [s.get(Generation, g).image_path for g in gens]
        other_key = s.get(Generation, other).image_path
        char_key = s.get(Generation, char_gen).image_path
    page_prefix = f"books/{b['id']}/pages/{p['id']}/"
    # 一個額外物件（例如舊資料的草圖）也要一起刪掉
    storage.put(f"{page_prefix}sketch.png", b"x")
    assert list(storage.list(page_prefix))
    assert list(storage.list(f"cache/previews/{gens[1]}_"))

    assert client.delete(f"/api/pages/{p['id']}").status_code == 204
    assert list(storage.list(page_prefix)) == []
    assert all(not storage.exists(k) for k in keys)
    assert list(storage.list(f"cache/previews/{gens[1]}_")) == []
    # 別頁與角色的物件不受影響
    assert storage.exists(other_key) and storage.exists(char_key)


def test_delete_page_prefix_has_trailing_slash(client, app):
    """books/1/pages/1/ 不可誤刪 books/1/pages/10/。"""
    b, _ = book_with_character(client, app)
    pages = [new_page(client, b["id"]) for _ in range(10)]
    first, tenth = pages[0], pages[9]
    assert str(tenth["id"]).startswith(str(first["id"]))
    storage = app.state.storage
    keep = f"books/{b['id']}/pages/{tenth['id']}/lineart_x.png"
    storage.put(keep, b"x")
    spy = SpyStorage(storage)
    app.state.storage = spy
    assert client.delete(f"/api/pages/{first['id']}").status_code == 204
    assert ("delete_prefix", f"books/{b['id']}/pages/{first['id']}/") in spy.calls
    assert storage.exists(keep)


# ---- r2 後端整條流程（moto，不連網）：證明沒有只認路徑的程式碼 ----------------------------


@pytest.fixture
def r2_app(settings_file, tmp_path, monkeypatch):
    boto3 = pytest.importorskip("boto3")
    moto = pytest.importorskip("moto")
    import socket

    from app import main as app_main
    from app.storage import CachedStorage, R2Storage

    def blocked(*args, **kwargs):
        raise RuntimeError(f"測試不可連網：{args!r}")

    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    for k in ("AWS_SESSION_TOKEN", "AWS_PROFILE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    with moto.mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="cb-test")
        r2 = R2Storage("https://s3.amazonaws.com", "cb-test", "AKTEST", "SKTEST")
        cached = CachedStorage(r2, tmp_path / "r2cache", 50_000_000)
        monkeypatch.setattr(app_main, "make_storage", lambda s: cached)
        app = create_app(load_settings(settings_file), engine=FakeEngine(), start_worker=False)
        yield app, r2


def test_r2_backend_full_flow(r2_app, settings_file):
    from tests.test_api_compose import compose_ready, confirm

    app, r2 = r2_app
    storage_dir = load_settings(settings_file).paths.storage_dir
    with TestClient(app) as client:
        b, p, bg, pose = compose_ready(client, app)
        gid = confirm(client, p["id"])["selected_generation_id"]
        for url in (f"/api/pages/{p['id']}/lineart", f"/api/pages/{p['id']}/qa/overlay.png",
                    f"/api/pages/{p['id']}/print.png", f"/api/generations/{gid}/image",
                    f"/api/generations/{pose}/mask", f"/api/generations/{pose}/cutout",
                    f"/api/generations/{bg}/lineart-preview"):
            r = client.get(url)
            assert r.status_code == 200, (url, r.text)
            assert r.content.startswith(b"\x89PNG"), url
        assert client.get(f"/api/pages/{p['id']}/qa").status_code == 200
        with app.state.sessions() as s:
            from app.models import Page

            lineart_key = s.get(Page, p["id"]).lineart_path
        assert r2.exists(lineart_key) and r2.exists(f"books/{b['id']}/generations/{gid}.png")
        # 線稿物件在 R2 被刪（例如 GC）：重算，不是 500
        app.state.storage.delete(lineart_key)
        assert client.get(f"/api/pages/{p['id']}/lineart").status_code == 200
        assert r2.exists(lineart_key)

        assert client.delete(f"/api/pages/{p['id']}").status_code == 204
        assert list(r2.list(f"books/{b['id']}/pages/{p['id']}/")) == []
        assert not r2.exists(f"books/{b['id']}/generations/{gid}.png")
    # 沒有任何圖片寫到本機資料夾（只有 SQLite 與簽章金鑰）
    assert not (storage_dir / "books").exists() and not (storage_dir / "cache").exists()


class FlakyDeleteStorage(SpyStorage):
    """delete／delete_prefix 對指定的 key 丟 OSError（模擬 R2 暫時失敗），其他照常。"""

    def __init__(self, inner: Storage, fail: set[str]):
        super().__init__(inner)
        self.fail = fail

    def __getattr__(self, name):
        attr = super().__getattr__(name)
        if name in ("delete", "delete_prefix"):
            def maybe_fail(key, *a, **kw):
                if key in self.fail:
                    self.calls.append((name, key))
                    raise OSError(f"boom: {key}")
                return attr(key, *a, **kw)
            return maybe_fail
        return attr


@pytest.mark.parametrize("which", ["first_image", "prefix", "preview"])
def test_delete_page_cleanup_continues_after_one_failure(client, app, which):
    """頁面刪除後清物件：某一個刪除失敗只記 log，其他物件照樣刪；回應仍是 204。"""
    app.state.engine.image = color_png()
    b, _ = book_with_character(client, app, **A5_HOME)
    p = new_page(client, b["id"])
    gens = [seed_generation(app, b["id"], p["id"], color_png()) for _ in range(2)]
    set_page(app, p["id"], selected_generation_id=gens[0])
    assert client.get(f"/api/pages/{p['id']}/lineart").status_code == 200
    assert client.get(f"/api/generations/{gens[1]}/lineart-preview").status_code == 200
    storage = app.state.storage
    with app.state.sessions() as s:
        keys = [s.get(Generation, g).image_path for g in gens]
    page_prefix = f"books/{b['id']}/pages/{p['id']}/"
    preview = next(iter(storage.list(f"cache/previews/{gens[1]}_")))
    failing = {"first_image": keys[0], "prefix": page_prefix, "preview": preview}[which]
    app.state.storage = FlakyDeleteStorage(storage, {failing})

    assert client.delete(f"/api/pages/{p['id']}").status_code == 204
    assert any(k == failing for _, k in app.state.storage.calls)  # 真的失敗過
    expect_gone = {
        "image": [k for k in keys if k != failing],
        "prefix": [] if failing == page_prefix else [page_prefix],
        "preview": [] if failing == preview else [preview],
    }
    assert all(not storage.exists(k) for k in expect_gone["image"])
    for prefix in expect_gone["prefix"]:
        assert list(storage.list(prefix)) == []
    for k in expect_gone["preview"]:
        assert not storage.exists(k)
